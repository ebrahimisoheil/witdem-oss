"""Load analytics SQL definitions from package-owned files."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

_QUERY_ROOT = Path(__file__).resolve().parents[1] / "queries"
_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
class QueryNotFoundError(FileNotFoundError):
    """Raised when a named analytics query does not exist."""


class QueryTemplateError(ValueError):
    """Raised when a query template is missing a required SQL fragment."""


def load_query(name: str, *, fragments: Mapping[str, str] | None = None) -> str:
    """Load one named SQL file and render only its approved structural fragments.

    Data values must remain DuckDB parameters and be passed separately to
    ``connection.execute``. Fragments are reserved for structural clauses such
    as a generated ``WHERE`` predicate or optional ``LIMIT`` clause.
    """

    filename = Path(name)
    if filename.is_absolute() or ".." in filename.parts or filename.suffix not in {"", ".sql"}:
        raise ValueError(f"invalid analytics query name: {name!r}")
    path = _QUERY_ROOT / (filename if filename.suffix else filename.with_suffix(".sql"))
    if not path.is_file():
        raise QueryNotFoundError(f"analytics query {name!r} was not found at {path}")
    query = path.read_text(encoding="utf-8")
    values = dict(fragments or {})
    placeholders = set(_PLACEHOLDER.findall(query))
    missing = sorted(placeholders - values.keys())
    if missing:
        raise QueryTemplateError(f"analytics query {name!r} requires fragments: {', '.join(missing)}")
    unused = sorted(values.keys() - placeholders)
    if unused:
        raise QueryTemplateError(f"analytics query {name!r} received unused fragments: {', '.join(unused)}")
    return _PLACEHOLDER.sub(lambda match: values[match.group(1)], query)
