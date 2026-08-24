"""Optional Product Factory semantic enrichment through Witdem's public SDK."""

from __future__ import annotations

from typing import Any


class WitdemSemanticSink:
    """Translate existing Product Factory semantic points into SDK records."""

    def __init__(self) -> None:
        try:
            import witdem_sdk  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - deployment-dependent
            raise RuntimeError(
                "Product Factory SDK enrichment is enabled, but the public witdem_sdk package is not installed."
            ) from exc
        self._sdk = witdem_sdk

    def emit(self, event_type: str, name: str, payload: dict[str, Any]) -> None:
        if event_type == "decision":
            self._sdk.decision(name, payload.get("selected", payload.get("value")), attributes=payload)
        elif event_type == "outcome":
            status = payload.get("status")
            self._sdk.outcome(
                name, status=str(status) if status is not None else None, value=payload, attributes=payload
            )
        elif event_type == "metric":
            self._sdk.metric(name, payload.get("value", 0), attributes=payload)
        elif event_type == "acceptance":
            self._sdk.outcome("accepted", status="accepted", value=payload, attributes=payload)
        else:
            self._sdk.event(name, payload)

    def evaluation(
        self, name: str, *, value: Any, score: float | None, attributes: dict[str, Any] | None = None
    ) -> None:
        self._sdk.evaluation(name, value=value, score=score, attributes=attributes)

    def outcome(self, name: str, *, status: str | None, value: Any, attributes: dict[str, Any] | None = None) -> None:
        self._sdk.outcome(name, status=status, value=value, attributes=attributes)
