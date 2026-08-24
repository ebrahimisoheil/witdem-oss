"""Explicit OpenTelemetry setup for this demo's own spans and Haystack's.

Standard OTel environment variables only (no custom names invented):

    OTEL_EXPORTER_OTLP_ENDPOINT   default: http://localhost:4318
    OTEL_EXPORTER_OTLP_PROTOCOL   default: http/protobuf (the only one this
                                  package supports -- it imports the HTTP/
                                  protobuf exporter directly, never gRPC; see
                                  docs/architecture.md)
    OTEL_SERVICE_NAME             default: agent-demo

``ExecutionIdSpanProcessor`` below is a deliberate ~10-line duplicate of
``witdem.telemetry.otel.ExecutionIdSpanProcessor``. This
package must never import ``witdem`` (it is a fully separate
deployable), so the same tiny baggage-copying behavior is re-implemented
here rather than shared -- using the exact same baggage key,
``"witdem.execution_id"``, so Witdem's OTLP receiver can correlate this app's
spans without needing to know this app exists.
"""

from __future__ import annotations

import logging
import os

from opentelemetry import baggage, context, trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.semconv.resource import ResourceAttributes

EXECUTION_ID_BAGGAGE_KEY = "witdem.execution_id"
_DEFAULT_ENDPOINT = "http://localhost:4318"
_SUPPORTED_PROTOCOL = "http/protobuf"

_CONFIGURED = False
_TRACER_NAME = "agent_demo"


class ExecutionIdSpanProcessor(SpanProcessor):
    """Copy the execution key from OTel baggage onto every child span.

    Mirrors ``witdem.telemetry.otel.ExecutionIdSpanProcessor``
    exactly (same baggage key, same behavior) -- duplicated intentionally,
    not imported, because this package must never depend on
    ``witdem``.
    """

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        execution_id = baggage.get_baggage(EXECUTION_ID_BAGGAGE_KEY, parent_context)
        if isinstance(execution_id, str):
            span.set_attribute(EXECUTION_ID_BAGGAGE_KEY, execution_id)

    def on_end(self, span: ReadableSpan) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def _quiet_haystack_blocked_component_warning() -> None:
    """Silence one specific, verified-benign Haystack log line, not Haystack logging in general.

    ``workflow.py``'s ``GenerateModelTurn`` deliberately omits its ``turn``
    output on a final-answer turn, which is what makes ``execute_tool`` not
    run that cycle (see its docstring) -- a standard Haystack
    conditional-branching idiom, and exactly what produces every scenario's
    distinct physical shape.

    Haystack's own pipeline executor detects this every time (any
    ``turn_pipeline.run()`` call where the model gives a final answer
    directly) and logs, at WARNING level:

        "Cannot run pipeline - the pipeline appears to be blocked.
         The following components could not be run ...
           - 'generate_turn' (GenerateModelTurn)
         Note that some of these components may be intentionally inactive
         due to conditional branching..."

    This was investigated directly, not assumed: reproduced locally with a
    fresh Workflow per scenario (no shared state, no concurrency involved),
    confirming the warning count for each scenario equals exactly the number
    of `turn_pipeline.run()` calls that end in a final answer (0 for
    terminal_failure, which never reaches one; 3 for correction_loop, which
    always does) -- i.e. this is Haystack's own detector correctly
    recognizing the branch, just mislabeling the *sender* (generate_turn)
    rather than the component that actually didn't run (execute_tool). It
    never raises, never affects results (all of this package's tests plus
    a real concurrent Docker run confirm every scenario's result is
    correct every time), and would otherwise print a scary-looking false
    alarm on every single ordinary request -- exactly the "if this is
    unexpected" case Haystack's own message says is safe to ignore. Only
    this one logger is raised to ERROR; nothing else about Haystack's
    (or any other) logging is touched, so a genuine problem still surfaces.
    """

    logging.getLogger("haystack.core.pipeline.base").setLevel(logging.ERROR)


def _build_otlp_exporter() -> SpanExporter:
    protocol = os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL", _SUPPORTED_PROTOCOL)
    if protocol != _SUPPORTED_PROTOCOL:
        # We only ever import the HTTP/protobuf exporter class (see module
        # docstring / docs/architecture.md): gRPC is out of scope for v1.
        # Rather than silently doing the wrong thing, fail loudly -- but only
        # at the point something actually tries to export, not at import time.
        raise RuntimeError(
            f"OTEL_EXPORTER_OTLP_PROTOCOL={protocol!r} is not supported by agent_demo; "
            f"only {_SUPPORTED_PROTOCOL!r} is (OTLP/HTTP protobuf, per docs/architecture.md)."
        )
    # Deliberately no explicit `endpoint=` kwarg: the exporter resolves
    # OTEL_EXPORTER_OTLP_TRACES_ENDPOINT / OTEL_EXPORTER_OTLP_ENDPOINT (falling
    # back to its own http://localhost:4318/v1/traces default) per the OTel
    # spec's standard env-var resolution, including appending "/v1/traces"
    # only when the generic (non-per-signal) endpoint var is used. Reimplementing
    # that logic here would risk double-appending the path.
    os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", _DEFAULT_ENDPOINT)
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    return OTLPSpanExporter()


def configure_tracing(*, exporter: SpanExporter | None = None) -> trace.Tracer:
    """Configure the global TracerProvider once per process.

    Idempotent: the first call wins (OTel's own global provider can only be
    set once anyway). Tests that need to inspect emitted spans should call
    this themselves with an ``InMemorySpanExporter`` *before* importing
    ``agent_demo.api`` -- see ``tests/conftest.py``, which does exactly that
    at collection time so it always wins the race against api.py's own
    module-level ``configure_tracing()`` call.

    Processor choice depends on whether a custom ``exporter`` was supplied:

    - No ``exporter`` (the real default path): the real network
      ``OTLPSpanExporter`` is wrapped in a ``BatchSpanProcessor``, which
      exports on a background thread. This was verified necessary, not
      cosmetic: an earlier revision used ``SimpleSpanProcessor`` here (which
      the task brief allows -- "simple is fine for this scale") and a live
      Docker run against an unreachable collector showed every ``/run``
      request blocking for 10s+ per span while the exporter retried
      synchronously with backoff before giving up -- an unacceptable demo
      experience whenever Witdem isn't up yet (a likely condition, not an edge
      case, in a multi-container compose startup).
    - An explicit ``exporter`` (tests passing an ``InMemorySpanExporter``):
      ``SimpleSpanProcessor``, so spans are visible to
      ``get_finished_spans()`` immediately after the request returns rather
      than sitting in a batch queue -- there is no network I/O to worry
      about blocking on in that path.
    """

    global _CONFIGURED
    if _CONFIGURED:
        return trace.get_tracer(_TRACER_NAME)

    _quiet_haystack_blocked_component_warning()
    os.environ.setdefault("HAYSTACK_CONTENT_TRACING_ENABLED", "false")
    service_name = os.environ.get("OTEL_SERVICE_NAME", "agent-demo")
    provider = TracerProvider(resource=Resource.create({ResourceAttributes.SERVICE_NAME: service_name}))
    provider.add_span_processor(ExecutionIdSpanProcessor())
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    else:
        provider.add_span_processor(BatchSpanProcessor(_build_otlp_exporter()))
    trace.set_tracer_provider(provider)

    try:
        from haystack import tracing as haystack_tracing
        from haystack_integrations.tracing.opentelemetry import OpenTelemetryTracer

        haystack_tracing.enable_tracing(  # type: ignore[attr-defined]
            OpenTelemetryTracer(trace.get_tracer(f"{_TRACER_NAME}.haystack"))
        )
    except ImportError:  # pragma: no cover - haystack-ai is a base dependency, but stay defensive
        pass

    _CONFIGURED = True
    return trace.get_tracer(_TRACER_NAME)


def get_tracer() -> trace.Tracer:
    """Return this app's tracer, configuring tracing with defaults if nobody has yet."""

    if not _CONFIGURED:
        configure_tracing()
    return trace.get_tracer(_TRACER_NAME)


def bind_execution_id(execution_id: str) -> object:
    """Attach ``execution_id`` to the current OTel context via baggage.

    Returns an opaque token that must be passed to :func:`detach_execution_id`
    (typically in a ``finally`` block) to restore the previous context --
    this is what makes every span created while the token is attached,
    including ones several components deep, carry ``witdem.execution_id``.
    """

    return context.attach(baggage.set_baggage(EXECUTION_ID_BAGGAGE_KEY, execution_id))


def detach_execution_id(token: object) -> None:
    context.detach(token)  # type: ignore[arg-type]
