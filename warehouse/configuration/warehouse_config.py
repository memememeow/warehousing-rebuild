"""Declare the immutable, validated startup snapshot.

The snapshot retains catalog references, traversal order, initial overrides,
employees and identifier settings. Numeric values are startup evidence; runtime
owners will use their own constants after one-time initialization.

Debug mode is a launcher-only console setting, not a snapshot field. Startup
and configured-value diagnostics use DEBUG under --debug T; this immutable
object owns no logging threshold or operational INFO output.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from warehouse.domain.location import Location, LocationMap
from warehouse.domain.product_catalog import ProductCatalog
from warehouse.domain.worker_type import WorkerType

# TODO: Copy and freeze mapping inputs before publishing validated settings.


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

