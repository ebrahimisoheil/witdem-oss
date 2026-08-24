"""Runtime adapter boundary: framework-specific span interpretation lives here.

The analytics core (``analytics/core.py``, ``analytics/identity.py``,
``analytics/runtime.py``) stays framework-neutral. Adapters translate a raw
runtime's span/attribute conventions into the canonical
``Execution``/``Operation``/``Link`` graph.
"""

from __future__ import annotations

from witdem.adapters.base import RuntimeAdapter

__all__ = ["RuntimeAdapter"]
