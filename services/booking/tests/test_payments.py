"""Payments: Checkout, signed webhooks, tickets with QR and refunds.

Stripe itself is replaced by a fake client; webhook payloads are signed exactly
like Stripe signs them, so signature verification is tested for real.
"""

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from types import SimpleNamespace
from typing import Any

import pytest
import stripe
from httpx import AsyncClient
from sqlalchemy import update

from booking import emails, payments, service, tasks, tickets
from booking.config import get_settings
from booking.db import SessionFactory
from booking.main import app, get_stripe_client
from booking.models import CatalogEvent, Reservation, ReservationStatus

from .conftest import CreateInventory, auth

ANA, LUIS = 1, 2
WEBHOOK_SECRET = "whsec_test_secret"


class FakeStripe:
    """Just enough of stripe.StripeClient for our code, recording every call."""

    def __init__(self) -> None:
        self.already_refunded = False
        self.created: list[dict[str, Any]] = []
        self.refunds: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.sessions: dict[str, SimpleNamespace] = {}
        sessions = SimpleNamespace(create=self._create, retrieve=self._retrieve)
        refunds = SimpleNamespace(create=self._refund)
        self.v1 = SimpleNamespace(
            checkout=SimpleNamespace(sessions=sessions), refunds=refunds
        )

    def _create(self, params: dict[str, Any]) -> SimpleNamespace:
        self.created.append(params)
        session_id = f"cs_test_{len(self.created)}"
        checkout = SimpleNamespace(
            id=session_id,
            url=f"https://checkout.stripe.test/{session_id}",
            status="open",
        )
        self.sessions[session_id] = checkout
        return checkout

    def _retrieve(self, session_id: str) -> SimpleNamespace:
        return self.sessions[session_id]

    def _refund(
        self, params: dict[str, Any], options: dict[str, Any]
    ) -> SimpleNamespace:
        if self.already_refunded:  # what Stripe says once the 24h key has expired
            raise stripe.InvalidRequestError(
                "Charge has already been refunded.",
                param=None,
                code="charge_already_refunded",
            )
        self.refunds.append((params, options))
        return SimpleNamespace(id=f"re_test_{len(self.refunds)}")


@pytest.fixture
def fake_stripe(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeStripe]:
    fake = FakeStripe()
    app.dependency_overrides[get_stripe_client] = lambda: fake
    monkeypatch.setattr(payments, "stripe_client", lambda settings: fake)
    yield fake
    app.dependency_overrides.pop(get_stripe_client, None)


class FakeSMTP:
    sent: list[EmailMessage] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        pass

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def send_message(self, message: EmailMessage) -> None:
        FakeSMTP.sent.append(message)


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> list[EmailMessage]:
    FakeSMTP.sent = []
    monkeypatch.setattr(emails.smtplib, "SMTP", FakeSMTP)
    return FakeSMTP.sent


async def make_reservation(
    create_inventory: CreateInventory,
    *,
    quantity: int = 2,
    price: str = "40.00",
    minutes_ago: int = 0,
) -> Reservation:
    inventory = await create_inventory(total=10, price=price)
    async with SessionFactory() as session:
        return await service.reserve(
            session,
            user_id=ANA,
            inventory_id=inventory.id,
            quantity=quantity,
            now=datetime.now(UTC) - timedelta(minutes=minutes_ago),
            ttl=timedelta(minutes=10),
            buyer_email="ana@example.com",
        )


async def load(reservation_id: uuid.UUID) -> Reservation:
    async with SessionFactory() as session:
        reservation = await session.get(Reservation, reservation_id)
        assert reservation is not None
        return reservation


def signed(payload: dict[str, Any], secret: str = WEBHOOK_SECRET) -> tuple[bytes, str]:
    """Body + Stripe-Signature header, computed the way Stripe does it."""
    body = json.dumps(payload).encode()
    timestamp = int(time.time())
    signature = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return body, f"t={timestamp},v1={signature}"


def checkout_completed(
    reservation_id: uuid.UUID, *, event_id: str = "evt_1", paid: bool = True
) -> dict[str, Any]:
    return {
        "id": event_id,
        "object": "event",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_1",
                "object": "checkout.session",
                "client_reference_id": str(reservation_id),
                "payment_status": "paid" if paid else "unpaid",
                "payment_intent": "pi_test_1",
            }
        },
    }


async def post_webhook(
    client: AsyncClient, payload: dict[str, Any], secret: str = WEBHOOK_SECRET
) -> Any:
    body, header = signed(payload, secret)
    return await client.post(
        "/webhooks/stripe",
        content=body,
        headers={"stripe-signature": header, "content-type": "application/json"},
    )


class TestCheckout:
    async def test_creates_a_stripe_page_with_the_right_amount(
        self,
        client: AsyncClient,
        create_inventory: CreateInventory,
        fake_stripe: FakeStripe,
    ) -> None:
        reservation = await make_reservation(
            create_inventory, quantity=2, price="40.00"
        )

        response = await client.post(
            f"/reservations/{reservation.id}/checkout", headers=auth(ANA)
        )

        assert response.status_code == 200
        assert response.json()["checkout_url"].startswith(
            "https://checkout.stripe.test/"
        )
        [params] = fake_stripe.created
        assert params["line_items"][0]["quantity"] == 2
        assert params["line_items"][0]["price_data"]["unit_amount"] == 4000  # cents
        assert params["client_reference_id"] == str(reservation.id)
        assert params["customer_email"] == "ana@example.com"

    async def test_asking_twice_reuses_the_same_page(
        self,
        client: AsyncClient,
        create_inventory: CreateInventory,
        fake_stripe: FakeStripe,
    ) -> None:
        reservation = await make_reservation(create_inventory)
        url = f"/reservations/{reservation.id}/checkout"
        first = (await client.post(url, headers=auth(ANA))).json()
        second = (await client.post(url, headers=auth(ANA))).json()
        assert first == second
        assert len(fake_stripe.created) == 1

    async def test_expired_reservation_cannot_be_paid(
        self,
        client: AsyncClient,
        create_inventory: CreateInventory,
        fake_stripe: FakeStripe,
    ) -> None:
        reservation = await make_reservation(create_inventory, minutes_ago=11)
        response = await client.post(
            f"/reservations/{reservation.id}/checkout", headers=auth(ANA)
        )
        assert response.status_code == 409
        assert fake_stripe.created == []

    async def test_someone_elses_reservation_is_404(
        self,
        client: AsyncClient,
        create_inventory: CreateInventory,
        fake_stripe: FakeStripe,
    ) -> None:
        reservation = await make_reservation(create_inventory)
        response = await client.post(
            f"/reservations/{reservation.id}/checkout", headers=auth(LUIS)
        )
        assert response.status_code == 404

    async def test_without_stripe_keys_it_says_so(
        self, client: AsyncClient, create_inventory: CreateInventory
    ) -> None:
        reservation = await make_reservation(create_inventory)
        response = await client.post(
            f"/reservations/{reservation.id}/checkout", headers=auth(ANA)
        )
        assert response.status_code == 503


class TestWebhook:
    async def test_forged_signature_is_rejected(
        self, client: AsyncClient, create_inventory: CreateInventory
    ) -> None:
        reservation = await make_reservation(create_inventory)
        response = await post_webhook(
            client, checkout_completed(reservation.id), secret="whsec_attacker"
        )
        assert response.status_code == 400
        assert (await load(reservation.id)).status == ReservationStatus.PENDING

    async def test_payment_confirms_and_queues_the_tickets(
        self,
        client: AsyncClient,
        create_inventory: CreateInventory,
        enqueued_tasks: list[tuple[str, str]],
    ) -> None:
        reservation = await make_reservation(create_inventory)

        response = await post_webhook(client, checkout_completed(reservation.id))

        assert response.json() == {"status": "confirmed"}
        stored = await load(reservation.id)
        assert stored.status == ReservationStatus.CONFIRMED
        assert stored.paid_at is not None
        assert stored.stripe_payment_intent_id == "pi_test_1"
        assert ("booking.send_tickets_email", str(reservation.id)) in enqueued_tasks

    async def test_repeated_event_is_processed_once(
        self,
        client: AsyncClient,
        create_inventory: CreateInventory,
        enqueued_tasks: list[tuple[str, str]],
    ) -> None:
        reservation = await make_reservation(create_inventory)
        await post_webhook(client, checkout_completed(reservation.id))
        again = await post_webhook(client, checkout_completed(reservation.id))
        assert again.json() == {"status": "duplicate"}
        ticket_tasks = [
            t for t in enqueued_tasks if t[0] == "booking.send_tickets_email"
        ]
        assert len(ticket_tasks) == 1

    async def test_late_payment_is_refunded(
        self,
        client: AsyncClient,
        create_inventory: CreateInventory,
        enqueued_tasks: list[tuple[str, str]],
    ) -> None:
        reservation = await make_reservation(create_inventory)
        async with SessionFactory() as session:  # expired/cancelled before paying
            await service.cancel(session, reservation_id=reservation.id, user_id=ANA)

        response = await post_webhook(client, checkout_completed(reservation.id))

        assert response.json() == {"status": "late"}
        stored = await load(reservation.id)
        assert stored.status == ReservationStatus.CANCELLED  # tickets stay released
        assert stored.paid_at is not None
        assert ("booking.refund_payment", str(reservation.id)) in enqueued_tasks

    async def test_unpaid_and_other_events_are_ignored(
        self, client: AsyncClient, create_inventory: CreateInventory
    ) -> None:
        reservation = await make_reservation(create_inventory)
        unpaid = await post_webhook(
            client, checkout_completed(reservation.id, paid=False)
        )
        other = await post_webhook(
            client, {**checkout_completed(reservation.id), "type": "charge.updated"}
        )
        assert unpaid.json() == other.json() == {"status": "ignored"}
        assert (await load(reservation.id)).status == ReservationStatus.PENDING


class TestTicketsAndRefunds:
    async def confirm(self, client: AsyncClient, reservation: Reservation) -> None:
        await post_webhook(client, checkout_completed(reservation.id))

    async def test_tickets_email_has_one_valid_qr_per_ticket(
        self,
        client: AsyncClient,
        create_inventory: CreateInventory,
        smtp: list[EmailMessage],
    ) -> None:
        reservation = await make_reservation(create_inventory, quantity=3)
        await self.confirm(client, reservation)

        assert await asyncio.to_thread(tasks.send_tickets_email, str(reservation.id))

        [message] = smtp
        attachments = list(message.iter_attachments())
        assert [a.get_filename() for a in attachments] == [
            "ticket-1.png",
            "ticket-2.png",
            "ticket-3.png",
        ]
        assert all(a.get_content().startswith(b"\x89PNG") for a in attachments)
        assert (await load(reservation.id)).tickets_sent_at is not None
        # Already sent: running the task again does nothing.
        assert not await asyncio.to_thread(
            tasks.send_tickets_email, str(reservation.id)
        )
        assert len(smtp) == 1

    async def test_late_payment_refund_is_idempotent(
        self,
        client: AsyncClient,
        create_inventory: CreateInventory,
        fake_stripe: FakeStripe,
    ) -> None:
        reservation = await make_reservation(create_inventory)
        async with SessionFactory() as session:
            await service.cancel(session, reservation_id=reservation.id, user_id=ANA)
        await post_webhook(client, checkout_completed(reservation.id))

        assert await asyncio.to_thread(tasks.refund_payment, str(reservation.id))
        assert not await asyncio.to_thread(tasks.refund_payment, str(reservation.id))

        [(params, options)] = fake_stripe.refunds
        assert params == {"payment_intent": "pi_test_1"}
        assert options == {"idempotency_key": f"refund-{reservation.id}"}
        stored = await load(reservation.id)
        assert stored.refunded_at is not None
        assert stored.stripe_refund_id == "re_test_1"

    async def test_sweep_finishes_work_whose_task_was_lost(
        self,
        client: AsyncClient,
        create_inventory: CreateInventory,
        fake_stripe: FakeStripe,
        smtp: list[EmailMessage],
    ) -> None:
        # Webhooks recorded the facts, but (enqueue patched out) no task ran.
        paid = await make_reservation(create_inventory)
        await self.confirm(client, paid)
        late = await make_reservation(create_inventory)
        async with SessionFactory() as session:
            await service.cancel(session, reservation_id=late.id, user_id=ANA)
        await post_webhook(client, {**checkout_completed(late.id), "id": "evt_2"})

        result = await asyncio.to_thread(tasks.sweep_payments)

        assert result == {"tickets_sent": 1, "refunded": 1}
        subjects = sorted(message["Subject"] for message in smtp)
        assert subjects == [
            "Your 2 TicketHub ticket(s)",
            "Your TicketHub payment has been refunded",
        ]
        assert len(fake_stripe.refunds) == 1


class TestEventCancellationRefunds:
    async def cancelled_paid_reservation(
        self, client: AsyncClient, create_inventory: CreateInventory
    ) -> Reservation:
        """A paid reservation whose event was then cancelled by the catalog."""
        reservation = await make_reservation(create_inventory)
        await post_webhook(client, checkout_completed(reservation.id))
        async with SessionFactory() as session:
            session.add(CatalogEvent(event_id=1, status="cancelled", version=9))
            await session.execute(
                update(Reservation)
                .where(Reservation.id == reservation.id)
                .values(status=ReservationStatus.CANCELLED)
            )
            await session.commit()
        return reservation

    async def test_refund_email_explains_the_event_was_cancelled(
        self,
        client: AsyncClient,
        create_inventory: CreateInventory,
        fake_stripe: FakeStripe,
        sent_emails: list[EmailMessage],
    ) -> None:
        reservation = await self.cancelled_paid_reservation(client, create_inventory)

        assert await asyncio.to_thread(tasks.refund_payment, str(reservation.id))

        assert len(fake_stripe.refunds) == 1
        [message] = sent_emails
        assert message["To"] == "ana@example.com"
        assert "80.00 € because the event was cancelled" in message.get_content()

    async def test_already_refunded_at_stripe_counts_as_done(
        self,
        client: AsyncClient,
        create_inventory: CreateInventory,
        fake_stripe: FakeStripe,
    ) -> None:
        reservation = await self.cancelled_paid_reservation(client, create_inventory)
        fake_stripe.already_refunded = True

        assert await asyncio.to_thread(tasks.refund_payment, str(reservation.id))

        stored = await load(reservation.id)
        assert stored.refunded_at is not None  # done, not failing forever
        assert stored.stripe_refund_id is None


class TestTicketCodes:
    KEY = "test-ticket-key"

    def test_genuine_codes_verify(self) -> None:
        reservation_id = uuid.uuid4()
        codes = tickets.ticket_codes(reservation_id, 2, self.KEY)
        assert [tickets.verify_ticket_code(c, self.KEY) for c in codes] == [
            (reservation_id, 1),
            (reservation_id, 2),
        ]

    def test_tampered_or_garbage_codes_are_rejected(self) -> None:
        [code] = tickets.ticket_codes(uuid.uuid4(), 1, self.KEY)
        reservation_id, _, signature = code.split(":")
        assert (
            tickets.verify_ticket_code(f"{reservation_id}:2:{signature}", self.KEY)
            is None
        )
        assert tickets.verify_ticket_code(code, "another-key") is None
        assert tickets.verify_ticket_code("not-a-ticket", self.KEY) is None


def test_signing_key_comes_from_settings() -> None:
    assert get_settings().ticket_signing_key.get_secret_value()
