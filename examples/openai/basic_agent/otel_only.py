"""OpenTelemetry-only entrypoint."""

from __future__ import annotations

from pathlib import Path

from app import run
from dotenv import load_dotenv
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

load_dotenv(Path(__file__).with_name(".env"))

provider = TracerProvider(resource=Resource.create({"service.name": "witdem-example-openai-basic-agent"}))
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)

with trace.get_tracer(__name__).start_as_current_span("witdem.execution") as span:
    span.set_attribute("witdem.example", "openai/basic_agent")
    execution_id = f"{span.get_span_context().trace_id:032x}"
    print(run())
provider.force_flush()
print(f"WITDEM_EXECUTION_ID={execution_id}")
