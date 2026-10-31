"""Declare accepted orders, pending batches and the canonical request registry.

Product and location validation precede identity allocation. Independent order
and request counters advance only on acceptance or creation. End-of-input
closure may form one final partial request and must remain idempotent.

Function entry/results, initialized numeric settings, accepted order IDs,
request creation and input closure use DEBUG under --debug T, including every
available order ID. Buffer diagnostics inside related commits; rejections use
WARNING and invariant failures use ERROR. Emit no separate INFO events.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Final

from warehouse.configuration.warehouse_config import WarehouseConfig
from warehouse.domain.fulfillment_request import FulfillmentRequest
from warehouse.domain.order import Order

# TODO: Initialize each numeric setting once after complete preflight validation.
ORDERS_PER_REQUEST: Final[int]
FIRST_ORDER_NUMBER: Final[int]
ORDER_MINIMUM_DIGITS: Final[int]
FIRST_REQUEST_NUMBER: Final[int]
REQUEST_MINIMUM_DIGITS: Final[int]


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
