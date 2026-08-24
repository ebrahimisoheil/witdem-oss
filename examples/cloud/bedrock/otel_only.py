"""Amazon Bedrock with standard OpenTelemetry only."""

from pathlib import Path

from app import run
from dotenv import load_dotenv
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

load_dotenv(Path(__file__).with_name(".env"))
provider = TracerProvider(resource=Resource.create({"service.name": "witdem-example-cloud-bedrock"}))
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)

with trace.get_tracer(__name__).start_as_current_span("bedrock.converse") as span:
    span.set_attribute("gen_ai.operation.name", "chat")
    span.set_attribute("gen_ai.provider.name", "aws.bedrock")
    result = run()
    span.set_attribute("gen_ai.response.model", result.model)
    if result.input_tokens is not None:
        span.set_attribute("gen_ai.usage.input_tokens", result.input_tokens)
    if result.output_tokens is not None:
        span.set_attribute("gen_ai.usage.output_tokens", result.output_tokens)
    execution_id = f"{span.get_span_context().trace_id:032x}"
    print(result.answer)
provider.force_flush()
print(f"WITDEM_EXECUTION_ID={execution_id}")
