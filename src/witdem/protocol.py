"""Public compatibility constants for Witdem's persisted and HTTP contracts."""

from typing import Final, Literal

SEMANTIC_RECORD_PROTOCOL_VERSION = "1.0"
DASHBOARD_API_VERSION = "1.0.0"
CORPUS_SCHEMA_VERSION = "1.0"
EVIDENCE_BUNDLE_SCHEMA_VERSION: Final[Literal["1.0"]] = "1.0"

__all__ = [
    "CORPUS_SCHEMA_VERSION",
    "DASHBOARD_API_VERSION",
    "EVIDENCE_BUNDLE_SCHEMA_VERSION",
    "SEMANTIC_RECORD_PROTOCOL_VERSION",
]
