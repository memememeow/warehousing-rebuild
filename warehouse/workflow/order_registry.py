"""Declare accepted orders, pending batches and the canonical request registry.

Product and location validation precede identity allocation. Independent order
and request counters advance only on acceptance or creation. End-of-input
closure may form one final partial request and must remain idempotent.

Accepted order IDs, request creation and input closure use DEBUG under --debug T,
including every available order ID. Buffer diagnostics inside related commits; rejections use
WARNING and invariant failures use ERROR. Emit no separate INFO events.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Final

from warehouse.configuration.warehouse_config import WarehouseConfig
from warehouse.domain.fulfillment_request import FulfillmentRequest
from warehouse.domain.order import Order

ORDERS_PER_REQUEST: Final[int]
FIRST_ORDER_NUMBER: Final[int]
ORDER_MINIMUM_DIGITS: Final[int]
FIRST_REQUEST_NUMBER: Final[int]
REQUEST_MINIMUM_DIGITS: Final[int]


def initialize_order_constants(config: WarehouseConfig) -> None:
    """Publish validated batch and identifier settings once before state exists."""
    global ORDERS_PER_REQUEST, FIRST_ORDER_NUMBER, ORDER_MINIMUM_DIGITS
    global FIRST_REQUEST_NUMBER, REQUEST_MINIMUM_DIGITS

    if any(name in globals() for name in (
        "ORDERS_PER_REQUEST", "FIRST_ORDER_NUMBER", "ORDER_MINIMUM_DIGITS",
        "FIRST_REQUEST_NUMBER", "REQUEST_MINIMUM_DIGITS",
    )):
        raise RuntimeError("order_registry numeric constants already initialized")

    (
        ORDERS_PER_REQUEST, FIRST_ORDER_NUMBER, ORDER_MINIMUM_DIGITS,
        FIRST_REQUEST_NUMBER, REQUEST_MINIMUM_DIGITS,
    ) = (
        config.orders_per_request, config.first_order_number, config.order_minimum_digits,
        config.first_request_number, config.request_minimum_digits,
    )


def __getattr__(name: str) -> object:
    """Reject numeric reads before explicit startup publication."""
    if name in (
        "ORDERS_PER_REQUEST", "FIRST_ORDER_NUMBER", "ORDER_MINIMUM_DIGITS",
        "FIRST_REQUEST_NUMBER", "REQUEST_MINIMUM_DIGITS",
    ):
        raise RuntimeError(f"order_registry.{name} read before numeric initialization")

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class OrderRegistry:
    """Own order acceptance, request formation, counters and input closure."""

    config: WarehouseConfig

    accepted_orders: dict[str, Order]
    pending_orders: deque[Order]
    requests: dict[int, FulfillmentRequest]

    next_order_number: int
    next_request_number: int
    input_closed: bool

    def __init__(self, config: WarehouseConfig) -> None:
        """Establish empty registries and counters from initialized owner constants."""
        ...

    def accept_order(self, model: str, colour: str) -> Order:
        """Validate the product and both locations before allocating an order ID."""
        ...

    def form_request(self, created_at: datetime) -> FulfillmentRequest | None:
        """Form the oldest full batch using an aware local creation timestamp."""
        ...

    def finish_orders(self, created_at: datetime) -> FulfillmentRequest | None:
        """Close input once and form remaining orders without repeating a batch."""
        ...
