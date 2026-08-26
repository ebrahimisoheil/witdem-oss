"""Hugging Face smolagents lifecycle integration."""

from __future__ import annotations

import threading
from collections.abc import Iterator, Mapping
from typing import Any

from witdem_sdk.integrations._common import ResultReporter, settings


class SmolagentsInstrumentation:
    """Registration around the official OpenInference smolagents instrumentor."""

    def __init__(self, instrumentor: Any, *, owned: bool) -> None:
        self.instrumentor = instrumentor
        self.owned = owned

    def disable(self) -> None:
        global _REGISTRATION
        with _REGISTRATION_LOCK:
            if self.owned:
                self.instrumentor.uninstrument()
                self.owned = False
            if _REGISTRATION is self:
                _REGISTRATION = None


_REGISTRATION_LOCK = threading.Lock()
_REGISTRATION: SmolagentsInstrumentation | None = None


def enable_smolagents() -> SmolagentsInstrumentation:
    """Enable the official smolagents OpenInference instrumentation once."""

    global _REGISTRATION
    with _REGISTRATION_LOCK:
        if _REGISTRATION is not None:
            return _REGISTRATION
        try:
            from openinference.instrumentation import TraceConfig  # type: ignore[import-not-found,unused-ignore]
            from openinference.instrumentation.smolagents import (  # type: ignore[import-not-found,unused-ignore]
                SmolagentsInstrumentor,
            )
        except ImportError as exc:
            raise ImportError("smolagents instrumentation requires 'witdem-sdk[smolagents]'") from exc
        instrumentor = SmolagentsInstrumentor()
        already_enabled = bool(getattr(instrumentor, "is_instrumented_by_opentelemetry", False))
        if not already_enabled:
            instrumentor.instrument(
                config=TraceConfig(
                    hide_llm_invocation_parameters=True,
                    hide_llm_tools=True,
                    hide_inputs=True,
                    hide_outputs=True,
                    hide_input_messages=True,
                    hide_output_messages=True,
                    hide_input_images=True,
                    hide_input_text=True,
                    hide_output_text=True,
                    hide_prompts=True,
                    hide_choices=True,
                )
            )
        _REGISTRATION = SmolagentsInstrumentation(instrumentor, owned=not already_enabled)
        return _REGISTRATION


class InstrumentedSmolagent:
    """Transparent agent proxy owning execution correlation and YAML evaluation."""

    def __init__(
        self,
        agent: Any,
        *,
        service_name: str | None,
        execution_name: str | None,
        endpoint: str | None,
        config_path: str | None,
        attributes: Mapping[str, Any] | None,
        report_result: ResultReporter | None,
    ) -> None:
        self._agent = agent
        self._settings = settings(
            service_name=service_name,
            execution_name=execution_name,
            endpoint=endpoint,
            config_path=config_path,
            attributes=attributes,
            report_result=report_result,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._agent, name)

    def run(self, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("stream") is True:
            return self._stream(*args, **kwargs)
        with self._settings.invocation() as witdem:
            enable_smolagents()
            result = self._agent.run(*args, **kwargs)
            self._settings.report(result, witdem)
            return result

    def _stream(self, *args: Any, **kwargs: Any) -> Iterator[Any]:
        with self._settings.invocation() as witdem:
            enable_smolagents()
            native = iter(self._agent.run(*args, **kwargs))
            last: Any = None
            while True:
                try:
                    last = next(native)
                except StopIteration as completed:
                    result = completed.value if completed.value is not None else getattr(last, "output", last)
                    self._settings.report(result, witdem)
                    return
                else:
                    yield last


def instrument(
    agent: Any,
    *,
    service_name: str | None = None,
    execution_name: str | None = None,
    endpoint: str | None = None,
    config_path: str | None = None,
    attributes: Mapping[str, Any] | None = None,
    report_result: ResultReporter | None = None,
) -> InstrumentedSmolagent:
    """Wrap a smolagents agent while preserving its native ``run`` API."""

    if isinstance(agent, InstrumentedSmolagent):
        return agent
    if not callable(getattr(agent, "run", None)):
        raise TypeError("smolagents instrument() expects an agent with a run() method")
    return InstrumentedSmolagent(
        agent,
        service_name=service_name,
        execution_name=execution_name,
        endpoint=endpoint,
        config_path=config_path,
        attributes=attributes,
        report_result=report_result,
    )
