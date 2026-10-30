"""Declare the immutable representation of an accepted customer order.

The registry allocates identity after product and location validation. Each
order preserves input model and colour spelling alongside resolved SKU strings,
and the same object remains attached to its fulfillment request.
"""

from __future__ import annotations

from dataclasses import dataclass

# TODO: Construct orders only after successful registry acceptance.


@dataclass(frozen=True, slots=True)
class Order:
    """Retain accepted identity, product spelling and the resolved fascia pair."""

    id: str
    model: str
    colour: str
    front_sku: str
    back_sku: str
