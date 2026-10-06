"""Structured logs and request ids."""

import json
import logging

import pytest
import structlog
from httpx import AsyncClient

from booking.config import get_settings
from booking.logging_config import configure_logging

from .conftest import auth


def logged_events(
    caplog: pytest.LogCaptureFixture, name: str
) -> list[dict[str, object]]:
    """structlog hands the event dict to stdlib logging: read it back from caplog."""
    return [
        record.msg
        for record in caplog.records
        if isinstance(record.msg, dict) and record.msg.get("event") == name
    ]


async def test_every_response_carries_a_request_id(client: AsyncClient) -> None:
    response = await client.get("/events/1/availability")
    assert len(response.headers["X-Request-ID"]) == 32


async def test_a_safe_incoming_request_id_is_kept(client: AsyncClient) -> None:
    response = await client.get(
        "/events/1/availability", headers={"X-Request-ID": "gateway-abc.123"}
    )
    assert response.headers["X-Request-ID"] == "gateway-abc.123"


async def test_a_suspicious_request_id_is_replaced(client: AsyncClient) -> None:
    forged = 'x" level=error event="fake log line'
    response = await client.get(
        "/events/1/availability", headers={"X-Request-ID": forged}
    )
    assert response.headers["X-Request-ID"] != forged


async def test_one_log_line_per_request_with_its_details(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    response = await client.get(
        "/reservations", headers={**auth(42), "X-Request-ID": "req-1"}
    )

    [line] = logged_events(caplog, "request")
    assert line["request_id"] == "req-1" == response.headers["X-Request-ID"]
    assert line["method"] == "GET"
    assert line["path"] == "/reservations"
    assert line["status_code"] == 200
    assert isinstance(line["duration_ms"], float)


def test_json_format_prints_one_json_object_per_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(level="INFO", fmt="json")
    try:
        structlog.contextvars.bind_contextvars(request_id="req-9")
        structlog.get_logger("test.json").info("snapshot_applied", event_id=20)
        logging.getLogger("some.library").warning("plain stdlib message")
    finally:
        structlog.contextvars.clear_contextvars()
        settings = get_settings()
        configure_logging(level=settings.log_level, fmt=settings.log_format)

    ours, library = (json.loads(line) for line in capsys.readouterr().out.splitlines())
    assert ours["event"] == "snapshot_applied"
    assert ours["event_id"] == 20
    assert ours["request_id"] == "req-9"  # context bound earlier is included
    assert ours["level"] == "info"
    assert "timestamp" in ours
    # Library logs (stdlib logging) come out in the same shape.
    assert library["event"] == "plain stdlib message"
    assert library["level"] == "warning"
