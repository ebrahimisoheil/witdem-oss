"""Storage-independent metadata selection for the public contract catalogue."""

from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any


def contract_definition_metadata(definition: Mapping[str, Any]) -> dict[str, Any]:
    """Copy only the reader's public definition fields, never event attributes.

    Nested definition values retain their existing public semantics. This is a
    metadata allowlist, not a sanitizer for content embedded in those values.
    """
    public_keys = (
        "contract_hash", "contract_name", "contract_version", "protocol_version",
        "service", "contract", "result", "decision", "product_goal",
        "evaluations", "metrics", "dimensions",
    )
    return deepcopy({key: definition[key] for key in public_keys if key in definition})


def summarize_contract_definitions(
    definitions: Iterable[Mapping[str, Any]], *, contract_hash: str | None = None,
) -> list[dict[str, Any]]:
    """Count one selected definition per execution, preserving first metadata.

    Callers select the execution population and its latest definition before
    calling this reducer. Preserve definition observation order (newest first,
    nulls last), including executions without reported goals. Missing hashes do
    not create synthetic contracts. This is not an evidence-history reader.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for definition in definitions:
        identity = str(definition.get("contract_hash") or "")
        if not identity or (contract_hash and identity != contract_hash):
            continue
        if identity not in grouped:
            grouped[identity] = {**contract_definition_metadata(definition), "run_count": 0}
        grouped[identity]["run_count"] += 1
    return sorted(grouped.values(), key=lambda item: (-int(item["run_count"]), str(item.get("contract_name") or "")))
