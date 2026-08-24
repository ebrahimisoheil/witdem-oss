"""Haystack native OpenTelemetry integration."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from witdem_sdk.integrations._common import IntegrationSettings, ResultReporter, settings

_HANDLES: dict[int, _HaystackHandle] = {}


class _HaystackHandle:
    def __init__(self, disable: Any, key: int) -> None:
        self._disable = disable
        self._key = key

    def disable(self) -> None:
        if self._disable is not None:
            self._disable()
            self._disable = None
            _HANDLES.pop(self._key, None)


def enable_haystack(witdem: Any, *, capture_content: bool = False) -> _HaystackHandle:
    """Enable Haystack's native tracer, using Witdem's active OTel provider."""

    key = id(witdem)
    existing = _HANDLES.get(key)
    if existing is not None:
        if existing._disable is None:
            _HANDLES.pop(key, None)
        else:
            return existing

    # Haystack's import-time auto-enabler can race its own partially
    # initialized tracing module. Witdem installs the native tracer explicitly,
    # so the automatic bootstrap is both redundant and harmful here.
    os.environ.setdefault("HAYSTACK_AUTO_TRACE_ENABLED", "false")
    try:
        from haystack.tracing import enable_tracing  # type: ignore[attr-defined,import-not-found,unused-ignore]
        from haystack_integrations.tracing.opentelemetry import (  # type: ignore[import-not-found,unused-ignore]
            OpenTelemetryTracer,
        )
        from opentelemetry import trace
    except ImportError as exc:
        raise ImportError("enable_haystack requires witdem-sdk[haystack] and haystack-ai") from exc
    tracer = OpenTelemetryTracer(trace.get_tracer("witdem.haystack"))
    enable_tracing(tracer)
    handle = _HaystackHandle(lambda: getattr(tracer, "disable", lambda: None)(), key)
    _HANDLES[key] = handle
    return handle


class InstrumentedPipeline:
    """Transparent Haystack pipeline with an SDK-owned run lifecycle."""

    def __init__(
        self,
        pipeline: Any,
        *,
        integration_settings: IntegrationSettings,
        capture_content: bool,
    ) -> None:
        self._pipeline = pipeline
        self._settings = integration_settings
        self._capture_content = capture_content

    def __getattr__(self, name: str) -> Any:
        return getattr(self._pipeline, name)

    def run(self, *args: Any, **kwargs: Any) -> Any:
        with self._settings.invocation() as witdem:
            handle = enable_haystack(witdem, capture_content=self._capture_content)
            try:
                result = self._pipeline.run(*args, **kwargs)
                self._settings.report(result, witdem)
                return result
            finally:
                handle.disable()

    async def run_async(self, *args: Any, **kwargs: Any) -> Any:
        with self._settings.invocation() as witdem:
            handle = enable_haystack(witdem, capture_content=self._capture_content)
            try:
                result = await self._pipeline.run_async(*args, **kwargs)
                self._settings.report(result, witdem)
                return result
            finally:
                handle.disable()


def instrument(
    pipeline: Any,
    *,
    service_name: str | None = None,
    execution_name: str | None = None,
    endpoint: str | None = None,
    config_path: str | None = None,
    capture_content: bool = False,
    attributes: Mapping[str, Any] | None = None,
    report_result: ResultReporter | None = None,
) -> InstrumentedPipeline:
    """Wrap a Haystack pipeline with automatic Witdem instrumentation."""

    if isinstance(pipeline, InstrumentedPipeline):
        return pipeline
    return InstrumentedPipeline(
        pipeline,
        integration_settings=settings(
            service_name=service_name,
            execution_name=execution_name,
            endpoint=endpoint,
            config_path=config_path,
            attributes=attributes,
            report_result=report_result,
        ),
        capture_content=capture_content,
    )
