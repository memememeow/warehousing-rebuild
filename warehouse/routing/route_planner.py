"""Declare the immutable query that plans pickup location occurrences.

The default route will retain incoming order and every duplicate occurrence.
Planning accepts locations rather than requests and never owns picking
progress, assignments or inventory mutation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from warehouse.domain.location import Location, LocationMap

# TODO: Freeze the supplied mapping and validate every requested location.


@dataclass(frozen=True, slots=True)
class RoutePlanner:
    """Hold only the read-only SKU location map used by route queries."""

    location_map: LocationMap

    def plan_route(self, locations: Sequence[Location]) -> tuple[Location, ...]:
        """Return all known location occurrences in their incoming order."""
        ...
