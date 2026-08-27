"""Witdem runtime analytics product package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("witdem-analytics")
except PackageNotFoundError:  # pragma: no cover - only an unpackaged source tree
    __version__ = "0+unknown"
