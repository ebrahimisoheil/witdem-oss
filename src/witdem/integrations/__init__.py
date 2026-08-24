"""Runtime integration boundaries for external AI telemetry.

The modules in this package are intentionally upstream-facing.  They turn
OTel, OpenInference, framework callbacks, or native runtime traces into the
existing domain-neutral Witdem analytics graph; they do not add canonical
framework entities.
"""

from witdem.integrations.models.normalized_operation import NormalizedOperation
from witdem.integrations.models.normalized_span import NormalizedSpan

__all__ = ["NormalizedOperation", "NormalizedSpan"]
