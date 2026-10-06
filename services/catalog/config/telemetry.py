"""OpenTelemetry tracing: spans for every request, SQL query and Redis call.

Spans go to the collector at OTEL_EXPORTER_OTLP_ENDPOINT (Jaeger locally) using
the standard OTLP protocol: swapping Jaeger for another backend needs no code.
No endpoint configured (CI, tests) = no tracing set up, nothing is sent.
"""

import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.django import DjangoInstrumentor
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def configure_telemetry(service_name: str) -> None:
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    # Batches spans in the background: tracing never slows a request down.
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    DjangoInstrumentor().instrument()  # one span per request
    PsycopgInstrumentor().instrument()  # one span per SQL query
    RedisInstrumentor().instrument()  # throttling counters
