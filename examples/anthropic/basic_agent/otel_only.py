"""OpenTelemetry-only Anthropic entrypoint."""

import os
from pathlib import Path

from anthropic import Anthropic
from app import run
from dotenv import load_dotenv
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

load_dotenv(Path(__file__).with_name(".env"))
provider = TracerProvider(resource=Resource.create({"service.name": "witdem-example-anthropic-basic-agent"}))
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)
client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
with trace.get_tracer(__name__).start_as_current_span("witdem.execution") as span:
    span.set_attribute("witdem.example", "anthropic/basic_agent")
    execution_id = f"{span.get_span_context().trace_id:032x}"
    span.set_attribute("witdem.runtime.kind", "workflow")
    span.set_attribute("witdem.runtime.name", "anthropic/basic_agent")
    with trace.get_tracer(__name__).start_as_current_span("anthropic.messages.create") as model_span:
        model_span.set_attribute("gen_ai.operation.name", "chat")
        model_span.set_attribute("gen_ai.provider.name", "anthropic")
        model_span.set_attribute("gen_ai.request.model", os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"))
        print(run(client))
provider.force_flush()
print(f"WITDEM_EXECUTION_ID={execution_id}")
