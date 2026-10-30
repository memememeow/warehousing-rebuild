"""Declare picking assignments, route validation and marshalling ownership.

Routes must cover every required SKU occurrence before dequeueing. Validated
scans reduce stock before progress advances, and marshalling requires every
pick. Rework prepends the same request with fresh progress and no stock credit.
Assignment tuples retain the request, route-ordered SKUs and completed count.
"""

from __future__ import annotations

from collections import deque

from warehouse.domain.fulfillment_request import FulfillmentRequest
from warehouse.domain.location import Location, LocationMap
from warehouse.routing.route_planner import RoutePlanner
from warehouse.workflow.inventory import Inventory, StockChange

# TODO: Commit status, queue membership and pick progress only after validation.

type PickingAssignment = tuple[FulfillmentRequest, tuple[str, ...], int]


class PickingWorkflow:
    """Own FIFO picking, occurrence progress and rework returned to the front."""

    picking_queue: deque[FulfillmentRequest]
    picker_assignments: dict[str, PickingAssignment]
    employees: frozenset[str]
    location_map: LocationMap

    def __init__(self, employees: frozenset[str], location_map: LocationMap) -> None:
        """Retain configured picker names and shared read-only SKU locations."""
        ...

    def enqueue_request(self, request: FulfillmentRequest) -> None:
        """Queue a newly created request and commit WAITING_FOR_PICKING status."""
        ...

    def return_request(self, request: FulfillmentRequest) -> None:
        """Prepend the same rework request with fresh progress and retained stock loss."""
        ...

    def assign_picker(self, worker: str, planner: RoutePlanner) -> tuple[Location, ...]:
        """Validate the worker and complete route before assigning the next request."""
        ...

    def expected_pick(self, worker: str) -> tuple[int, str, Location]:
        """Expose the next positive pick number, expected SKU and mapped location."""
        ...

    def confirm_pick(
        self,
        worker: str,
        pick_number: int,
        scanned_sku: str,
        inventory: Inventory,
    ) -> StockChange:
        """Validate assignment and evidence, then reduce stock before advancing progress."""
        ...

    def release_to_marshalling(self, worker: str) -> FulfillmentRequest:
        """Release a fully picked request and commit READY_FOR_SEQUENCING status."""
        ...
