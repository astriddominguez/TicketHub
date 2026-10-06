"""OpenTelemetry tracing for the booking processes (API, consumer, Celery worker).

Spans go to the collector at OTEL_EXPORTER_OTLP_ENDPOINT (Jaeger locally) using
the standard OTLP protocol. No endpoint configured (CI, tests) = no tracing set
up, nothing is sent. (Not OTEL_SDK_DISABLED: the SDK itself obeys that one, which
would also switch off the in-memory tracer the tests use.)
"""

import os

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def telemetry_enabled() -> bool:
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))


def configure_telemetry(service_name: str, *, app: FastAPI | None = None) -> None:
    if not telemetry_enabled():
        return
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    # Batches spans in the background: tracing never slows a request down.
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    AsyncPGInstrumentor().instrument()  # every SQL query, from any engine
    RedisInstrumentor().instrument()  # cache and rate-limit calls
    # Celery: the sending side adds the trace to the task's headers, the worker
    # continues it. So "webhook -> send tickets email" is one trace.
    CeleryInstrumentor().instrument()
    if app is not None:
        # /health is called every few seconds by probes: not worth a trace each.
        FastAPIInstrumentor.instrument_app(app, excluded_urls="health")
