"""Own one truck's loaded requests and capacity query.

LoadingWorkflow supplies truck identifiers and controls order and dispatch.
Capacity counts requests, including a partial final request. Output consumes
canonical loaded contents rather than a separate archive of copied orders.

Useful load and committed-content details use DEBUG under --debug T.
Buffer nested diagnostics until the owning loading commit finishes. Problems
use WARNING/ERROR; emit no INFO.
"""

from __future__ import annotations

import logging
from typing import Final

from warehouse.configuration.warehouse_config import WarehouseConfig
from warehouse.domain.fulfillment_request import FulfillmentRequest

logger = logging.getLogger("warehouse")

CAPACITY_REQUESTS: Final[int]


def initialize_truck_constants(config: WarehouseConfig) -> None:
    """Publish the validated request-count capacity once before state exists."""
    global CAPACITY_REQUESTS

    if "CAPACITY_REQUESTS" in globals():
        raise RuntimeError("truck numeric constants already initialized")

    CAPACITY_REQUESTS = config.truck_capacity_requests


def __getattr__(name: str) -> object:
    """Reject numeric reads before explicit startup publication."""
    if name == "CAPACITY_REQUESTS":
        raise RuntimeError(f"truck.{name} read before numeric initialization")

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class Truck:
    """Own loaded requests while preventing duplicates and capacity overflow."""

    id: str
    requests: list[FulfillmentRequest]

    def __init__(self, truck_id: str) -> None:
        """Establish truck identity and an initially empty loaded collection."""
        self.id = truck_id
        self.requests = []

    @property
    def capacity_requests(self) -> int:
        """Expose the owning module's configured capacity without a setter."""
        if "CAPACITY_REQUESTS" not in globals():
            raise RuntimeError("truck.CAPACITY_REQUESTS read before numeric initialization")

        return CAPACITY_REQUESTS

    def record_request(self, request: FulfillmentRequest) -> None:
        """Record one successful load after duplicate and capacity validation."""
        capacity = self.capacity_requests
        count_before = len(self.requests)
        reason = None
        if request in self.requests:
            reason = "duplicate_request"
        elif count_before >= capacity:
            reason = "capacity_exceeded"

        if reason is not None:
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "function=truck.record_request action=rejected reason=%s", reason,
                    extra={
                        "truck_id": self.id, "request_id": request.id,
                        "request_sequence": request.sequence,
                        "order_ids": tuple(order.id for order in request.orders),
                        "status_before": request.status, "status_after": request.status,
                        "capacity_requests": capacity,
                        "request_count_before": count_before,
                        "request_count_after": count_before,
                    },
                )
            raise ValueError(
                f"Truck {self.id} rejected request {request.id}: {reason}"
            )

        self.requests.append(request)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "function=truck.record_request action=recorded",
                extra={
                    "truck_id": self.id, "request_id": request.id,
                    "request_sequence": request.sequence,
                    "order_ids": tuple(order.id for order in request.orders),
                    "status_before": request.status, "status_after": request.status,
                    "capacity_requests": capacity,
                    "request_count_before": count_before,
                    "request_count_after": len(self.requests),
                },
            )

    def is_full(self) -> bool:
        """Report whether loaded requests have reached the configured capacity."""
        return len(self.requests) >= self.capacity_requests
