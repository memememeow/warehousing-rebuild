"""Declare the immutable representation of an accepted customer order.

The registry allocates identity after product and location validation. Each
order preserves input model and colour spelling alongside resolved SKU strings,
and the same object remains attached to its fulfillment request.

Construction and accepted-order details use DEBUG under --debug T; the accepted
Order command's sole INFO mirror belongs to ActivityLog.record_command. The
immutable order has no debug-mode field.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("warehouse")

# TODO: Construct orders only after successful registry acceptance.


@dataclass(frozen=True, slots=True)
class Order:
    """Retain accepted identity, product spelling and the resolved fascia pair."""

    id: str
    model: str
    colour: str
    front_sku: str
    back_sku: str

    def __post_init__(self) -> None:
        """Trace generated construction without changing accepted order identity."""
        for action in ("entry", "returned"):
            logger.debug(
                "function=warehouse.domain.order.Order.__post_init__ action=%s "
                "constructor=warehouse.domain.order.Order.__init__ model=%s colour=%s",
                action, self.model, self.colour,
                extra={"order_ids": (self.id,), "sku_evidence": (self.front_sku, self.back_sku)},
            )
