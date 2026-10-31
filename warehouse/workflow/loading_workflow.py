"""Declare numeric loading order, staged evidence and truck replacement.

Only the next request sequence may be assigned. A successful final rescan must
record the load, advance sequence, update status and clear the assignment in
one commit. Full trucks depart immediately; a partial truck remains loaded.

Function entry/results, initialized identifier settings, assignments, evidence,
status changes, successful loads, truck dispatch/replacement and partial-truck
completion details use DEBUG under --debug T. Retain full request/order/truck
context; buffer nested diagnostics until commits finish. Problems retain
WARNING/ERROR, while only ActivityLog.record_command emits INFO.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from warehouse.configuration.warehouse_config import WarehouseConfig
from warehouse.domain.fulfillment_request import FulfillmentRequest
from warehouse.domain.truck import Truck

# TODO: Initialize truck ID settings once; preserve global sequence across trucks.
FIRST_TRUCK_NUMBER: Final[int]
TRUCK_MINIMUM_DIGITS: Final[int]

type LoadingAssignment = tuple[FulfillmentRequest, tuple[str, ...] | None]


class LoadingWorkflow:
    """Own ordered waiting work, active load evidence and canonical truck history."""

    config: WarehouseConfig

    loading_queue: dict[int, FulfillmentRequest]
    loader_assignments: dict[str, LoadingAssignment]
    
    next_request_number: int
    next_truck_number: int
    current_truck: Truck
    dispatched_trucks: list[Truck]

    def __init__(self, config: WarehouseConfig) -> None:
        """Establish loading collections, the first truck and numeric expectations."""
        ...

    def enqueue_request(self, request: FulfillmentRequest) -> None:
        """Buffer an already-ready request under its independent numeric sequence."""
        ...

    def assign_loader(self, worker: str) -> FulfillmentRequest:
        """Assign only the next numeric request and commit LOADING status."""
        ...

    def load_request(
        self, worker: str, request_number: int, sku_order: Sequence[str]
    ) -> None:
        """Validate and stage front/back pairs before awaiting the final rescan."""
        ...

    def confirm_rescan(
        self, worker: str, request_number: int, scanned_skus: Sequence[str]
    ) -> FulfillmentRequest:
        """Commit a verified load once while preserving retry state on rejection."""
        ...

    def dispatch_truck(self) -> Truck:
        """Archive a full truck, dispatch its requests and allocate its replacement."""
        ...
