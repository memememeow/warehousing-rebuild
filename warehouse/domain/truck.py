"""Declare one truck's loaded requests and capacity query.

LoadingWorkflow supplies truck identifiers and controls order and dispatch.
Capacity counts requests, including a partial final request. Output consumes
canonical loaded contents rather than a separate archive of copied orders.
"""

from __future__ import annotations

from typing import Final

from warehouse.domain.fulfillment_request import FulfillmentRequest

# TODO: Initialize capacity once from validated configuration before state exists.
CAPACITY_REQUESTS: Final[int]


class Truck:
    """Own loaded requests while preventing duplicates and capacity overflow."""

    id: str
    requests: list[FulfillmentRequest]

    def __init__(self, truck_id: str) -> None:
        """Establish truck identity and an initially empty loaded collection."""
        ...

    @property
    def capacity_requests(self) -> int:
        """Expose the owning module's configured capacity without a setter."""
        ...

    def record_request(self, request: FulfillmentRequest) -> None:
        """Record one successful load after duplicate and capacity validation."""
        ...

    def is_full(self) -> bool:
        """Report whether loaded requests have reached the configured capacity."""
        ...
