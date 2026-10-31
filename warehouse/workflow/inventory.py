"""Declare authoritative stock quantities and synchronous replenishment.

Initial quantities follow traversal order with validated overrides. A pick
must decrement and complete any threshold refill before progress advances.
Reserves are unlimited; sequencing rework never credits previous reductions.
StockChange records location, before, after decrement and final quantity;
manual refill uses the reset quantity for both after and final values.

All function entry/results, initialized numeric values, quantity queries, stock
decrements, threshold decisions, refill changes and snapshots use DEBUG under
--debug T. Buffer diagnostics until related pick and inventory commits finish;
problems retain WARNING/ERROR. Inventory emits no INFO lifecycle records.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from typing import Final

from warehouse.configuration.warehouse_config import WarehouseConfig
from warehouse.domain.location import Location

# TODO: Initialize defaults once and implement stock commits without boundary I/O.
DEFAULT_STOCK: Final[int]
REPLENISHMENT_THRESHOLD: Final[int]

type StockChange = tuple[Location, int, int, int]


class Inventory:
    """Own every mapped quantity and the synchronously drained refill queue."""

    quantities: dict[Location, int]
    replenishment_queue: deque[Location]

    def __init__(self, config: WarehouseConfig) -> None:
        """Initialize mapped locations at default stock, then apply overrides."""
        ...

    def quantity_at(self, location: Location) -> int:
        """Read a known location's quantity without adding inventory entries."""
        ...

    def decrement_stock(self, location: Location) -> StockChange:
        """Validate stock, decrement once and finish any immediate threshold refill."""
        ...

    def replenish_stock(self, location: Location) -> StockChange:
        """Reset a known location to default stock independently of worker readiness."""
        ...

    def snapshot(self) -> Mapping[Location, int]:
        """Return a defensive read-only copy of all stock in traversal order."""
        ...
