"""Declare fixed request data and its privately controlled lifecycle status.

Identity, numeric sequence, orders and expected sequencing evidence are exposed
read-only. The same request survives every handoff and rework attempt. Status
transitions must be validated before related workflow collections change.
"""

from __future__ import annotations

from warehouse.domain.order import Order
from warehouse.domain.request_status import RequestStatus

# TODO: Initialize fixed data and enforce lifecycle transitions without I/O.


class FulfillmentRequest:
    """Keep canonical orders and expected SKU occurrences through fulfillment."""

    __slots__ = ("_id", "_sequence", "_orders", "_correct_sku_order", "_status")

    _id: str
    _sequence: int
    _orders: tuple[Order, ...]
    _correct_sku_order: tuple[str, ...]
    _status: RequestStatus

    def __init__(
        self,
        request_id: str,
        sequence: int,
        orders: tuple[Order, ...],
        correct_sku_order: tuple[str, ...],
    ) -> None:
        """Retain immutable request data and establish the CREATED status."""
        ...

    @property
    def id(self) -> str:
        """Expose the full request identifier retained across rework."""
        ...

    @property
    def sequence(self) -> int:
        """Expose the independent numeric sequence used to order loading."""
        ...

    @property
    def orders(self) -> tuple[Order, ...]:
        """Expose the original accepted order objects in arrival order."""
        ...

    @property
    def correct_sku_order(self) -> tuple[str, ...]:
        """Expose N front occurrences followed by N back occurrences."""
        ...

    @property
    def status(self) -> RequestStatus:
        """Expose the last committed lifecycle status without a public setter."""
        ...

    def transition_to(self, next_status: RequestStatus) -> None:
        """Commit a legal lifecycle transition and reject all other transitions."""
        ...

    def loading_skus(self) -> tuple[str, ...]:
        """Derive interleaved front and back pairs without losing duplicates."""
        ...
