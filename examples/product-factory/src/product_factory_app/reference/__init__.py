"""Authoritative cross-runtime Product Factory reference workload."""

from product_factory_app.reference.contracts import CaseDefinition, ProductFactoryResult
from product_factory_app.reference.runner import run_case

__all__ = ["CaseDefinition", "ProductFactoryResult", "run_case"]
