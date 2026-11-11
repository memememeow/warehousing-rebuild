"""Declare fixed request data and its privately controlled lifecycle status.

Identity, numeric sequence, orders and expected sequencing evidence are exposed
read-only. The same request survives every handoff and rework attempt. Status
transitions must be validated before related workflow collections change.

Useful rejection evidence and committed status transitions
use DEBUG under --debug T, including all available request and order IDs. Buffer
nested diagnostics until the owning workflow commit finishes; no direct INFO
events or debug-mode properties belong on the request.
"""

from __future__ import annotations

import logging
from types import MappingProxyType

from warehouse.domain.order import Order
from warehouse.domain.request_status import RequestStatus

logger = logging.getLogger("warehouse")

_ALLOWED_TRANSITIONS = MappingProxyType({
    RequestStatus.CREATED: (RequestStatus.WAITING_FOR_PICKING,),
    RequestStatus.WAITING_FOR_PICKING: (RequestStatus.PICKING,),
    RequestStatus.PICKING: (RequestStatus.READY_FOR_SEQUENCING,),
    RequestStatus.READY_FOR_SEQUENCING: (RequestStatus.SEQUENCING,),
    RequestStatus.SEQUENCING: (RequestStatus.AWAITING_SEQUENCE_RESCAN,),
    RequestStatus.AWAITING_SEQUENCE_RESCAN: (
        RequestStatus.READY_FOR_LOADING, RequestStatus.REWORK_REQUIRED,
    ),
    RequestStatus.REWORK_REQUIRED: (RequestStatus.WAITING_FOR_PICKING,),
    RequestStatus.READY_FOR_LOADING: (RequestStatus.LOADING,),
    RequestStatus.LOADING: (RequestStatus.AWAITING_LOAD_RESCAN,),
    RequestStatus.AWAITING_LOAD_RESCAN: (RequestStatus.LOADED,),
    RequestStatus.LOADED: (RequestStatus.DISPATCHED,),
    RequestStatus.DISPATCHED: (),
})


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
        self._id = request_id
        self._sequence = sequence
        self._orders = tuple(orders)
        self._correct_sku_order = tuple(correct_sku_order)
        self._status = RequestStatus.CREATED

    @property
    def id(self) -> str:
        """Expose the full request identifier retained across rework."""
        return self._id

    @property
    def sequence(self) -> int:
        """Expose the independent numeric sequence used to order loading."""
        return self._sequence

    @property
    def orders(self) -> tuple[Order, ...]:
        """Expose the original accepted order objects in arrival order."""
        return self._orders

    @property
    def correct_sku_order(self) -> tuple[str, ...]:
        """Expose N front occurrences followed by N back occurrences."""
        return self._correct_sku_order

    @property
    def status(self) -> RequestStatus:
        """Expose the last committed lifecycle status without a public setter."""
        return self._status

    def transition_to(self, next_status: RequestStatus) -> None:
        """Commit a legal edge or raise ValueError without changing request data.

        Active owners use activity_log.buffered_diagnostics around their related
        in-memory updates so nested diagnostics reach transport after the commit.
        Direct calls deliver diagnostics after this request's status commit.
        """
        before = self._status
        if (
            not isinstance(next_status, RequestStatus)
            or next_status not in _ALLOWED_TRANSITIONS[before]
        ):
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "function=fulfillment_request.transition_to "
                    "action=rejected reason=illegal_transition requested_status=%s",
                    next_status,
                    extra={
                        "request_id": self._id, "request_sequence": self._sequence,
                        "order_ids": tuple(order.id for order in self._orders),
                        "sku_evidence": self._correct_sku_order,
                        "status_before": before, "status_after": before,
                    },
                )
            raise ValueError(
                f"Illegal request status transition: {before.value} -> {next_status!r} "
                f"for request {self._id}"
            )

        self._status = next_status
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "function=fulfillment_request.transition_to action=transitioned",
                extra={
                    "request_id": self._id, "request_sequence": self._sequence,
                    "order_ids": tuple(order.id for order in self._orders),
                    "sku_evidence": self._correct_sku_order,
                    "status_before": before, "status_after": next_status,
                },
            )

    def loading_skus(self) -> tuple[str, ...]:
        """Derive interleaved front and back pairs without losing duplicates."""
        midpoint = len(self._correct_sku_order) // 2
        return tuple(
            sku
            for pair in zip(
                self._correct_sku_order[:midpoint],
                self._correct_sku_order[midpoint:],
                strict=True,
            )
            for sku in pair
        )
