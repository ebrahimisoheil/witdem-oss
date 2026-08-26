"""Versioned built-in pricing catalog metadata."""

from importlib.resources import files

import yaml

_metadata = yaml.safe_load(files("witdem.pricing").joinpath("catalog.yaml").read_text(encoding="utf-8"))
CATALOG_VERSION = str(_metadata["catalog_version"])

__all__ = ["CATALOG_VERSION"]
