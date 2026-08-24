"""OpenTelemetry-only Haystack entrypoint."""

import os
from pathlib import Path

os.environ.setdefault("HAYSTACK_AUTO_TRACE_ENABLED", "false")

from app import run
from dotenv import load_dotenv
from haystack.tracing import enable_tracing
from haystack_integrations.tracing.opentelemetry import OpenTelemetryTracer
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

load_dotenv(Path(__file__).with_name(".env"))

provider = TracerProvider(resource=Resource.create({"service.name": "witdem-example-haystack"}))
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)
enable_tracing(OpenTelemetryTracer(trace.get_tracer("haystack")))
with trace.get_tracer(__name__).start_as_current_span("witdem.execution") as span:
    span.set_attribute("witdem.example", "haystack/pipeline")
    execution_id = f"{span.get_span_context().trace_id:032x}"
    print(run())
provider.force_flush()
print(f"WITDEM_EXECUTION_ID={execution_id}")
