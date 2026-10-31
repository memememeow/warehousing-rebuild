"""Declare immutable model and colour lookup for front and back SKU pairs.

Lookup keys use trimmed, case-folded product text while accepted orders retain
their display spelling. Construction must reject normalized collisions and
freeze a defensive copy so later input mutation cannot change the catalog.

Constructor and lookup entry/results, normalized keys and resolved SKU pairs
use DEBUG under --debug T. Unknown products retain recoverable rejection
handling; this query owner emits no INFO and stores no debug-mode setting.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

# TODO: Normalize and freeze product entries; reject unknown lookup pairs.


@dataclass(frozen=True, slots=True, init=False)
class ProductCatalog:
    """Provide read-only product translation without allocating order identity."""

    products: Mapping[tuple[str, str], tuple[str, str]]

    def __init__(
        self, products: Mapping[tuple[str, str], tuple[str, str]]
    ) -> None:
        """Validate normalized product keys and retain a frozen defensive copy."""
        ...

    def resolve_skus(self, model: str, colour: str) -> tuple[str, str]:
        """Resolve the fascia pair case-insensitively or raise ValueError."""
        ...
