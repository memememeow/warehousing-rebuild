"""Retain the immutable, validated startup snapshot.

The snapshot retains catalog references, traversal order, initial overrides,
employees and identifier settings. Numeric values are startup evidence; runtime
owners will use their own constants after one-time initialization.

Debug mode is a launcher-only console setting, not a snapshot field. This
immutable object freezes its mappings without routine constructor logs and
owns no logging threshold or operational INFO output.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from warehouse.domain.location import Location, LocationMap
from warehouse.domain.product_catalog import ProductCatalog
from warehouse.domain.worker_type import WorkerType


@dataclass(frozen=True, slots=True)
class WarehouseConfig:
    """Retain validated settings without owning counters or workflow progress."""

    translation_path: Path
    location_map_path: Path
    initial_inventory_path: Path

    catalog: ProductCatalog

    location_map: LocationMap
    initial_stock: Mapping[Location, int]
    employees: Mapping[WorkerType, tuple[str, ...]]

    default_stock: int
    replenishment_threshold: int

    orders_per_request: int
    pallet_capacity_fascia: int
    truck_capacity_requests: int

    order_minimum_digits: int
    request_minimum_digits: int
    truck_minimum_digits: int

    first_order_number: int
    first_request_number: int
    first_truck_number: int

    request_timestamp_format: str

    def __post_init__(self) -> None:
        """Freeze defensive reference copies; ConfigLoader validates scalar inputs."""
        locations = MappingProxyType(dict(self.location_map))
        stock = MappingProxyType(dict(self.initial_stock))
        employees = MappingProxyType({role: tuple(names) for role, names in self.employees.items()})
        object.__setattr__(self, "location_map", locations)
        object.__setattr__(self, "initial_stock", stock)
        object.__setattr__(self, "employees", employees)

