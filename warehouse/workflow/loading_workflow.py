"""Declare numeric loading order, staged evidence and truck replacement.

Only the next request sequence may be assigned. A successful final rescan must
record the load, advance sequence, update status and clear the assignment in
one commit. Full trucks depart immediately; a partial truck remains loaded.

Assignments, evidence, status changes, successful loads, truck
dispatch/replacement and partial-truck
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
from warehouse.workflow import order_registry

FIRST_TRUCK_NUMBER: Final[int]
TRUCK_MINIMUM_DIGITS: Final[int]

type LoadingAssignment = tuple[FulfillmentRequest, tuple[str, ...] | None]


def initialize_loading_constants(config: WarehouseConfig) -> None:
    """Publish validated truck identifier settings once before state exists."""
    global FIRST_TRUCK_NUMBER, TRUCK_MINIMUM_DIGITS

    if any(name in globals() for name in ("FIRST_TRUCK_NUMBER", "TRUCK_MINIMUM_DIGITS")):
        raise RuntimeError("loading_workflow numeric constants already initialized")

    FIRST_TRUCK_NUMBER, TRUCK_MINIMUM_DIGITS = (
        config.first_truck_number, config.truck_minimum_digits,
    )


def __getattr__(name: str) -> object:
    """Reject numeric reads before explicit startup publication."""
    if name in ("FIRST_TRUCK_NUMBER", "TRUCK_MINIMUM_DIGITS"):
        raise RuntimeError(f"loading_workflow.{name} read before numeric initialization")

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
        """Retain configuration and establish empty collections and numeric starts.

        Step 19 creates the first truck and advances next_truck_number only on
        successful creation. It must then preserve the independent request
        expectation across dispatch and use this module's truck ID constants.
        """
        if "FIRST_TRUCK_NUMBER" not in globals():
            raise RuntimeError("loading_workflow.FIRST_TRUCK_NUMBER read before numeric initialization")

        next_request_number = order_registry.FIRST_REQUEST_NUMBER
        self.config = config
        self.loading_queue = {}
        self.loader_assignments = {}
        self.next_request_number = next_request_number
        self.next_truck_number = FIRST_TRUCK_NUMBER
        self.dispatched_trucks = []

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
