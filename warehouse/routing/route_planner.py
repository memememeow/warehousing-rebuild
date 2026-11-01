"""Declare the immutable query that plans pickup location occurrences.

The default route will retain incoming order and every duplicate occurrence.
Planning accepts locations rather than requests and never owns picking
progress, assignments or inventory mutation.

Constructor and plan_route entry/results, known-location validation and route
occurrences use DEBUG under --debug T. Keep only location_map as planner state;
console verbosity belongs to the launcher and this query emits no INFO.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from warehouse.domain.location import Location, LocationMap

logger = logging.getLogger("warehouse")

# TODO: Freeze the supplied mapping and validate every requested location.


@dataclass(frozen=True, slots=True)
class RoutePlanner:
    """Hold only the read-only SKU location map used by route queries."""

    location_map: LocationMap

    def __post_init__(self) -> None:
        """Trace generated construction; map freezing belongs to the routing step."""
        if logger.isEnabledFor(logging.DEBUG):
            for action in ("entry", "returned"):
                logger.debug(
                    "function=warehouse.routing.route_planner.RoutePlanner.__post_init__ "
                    "action=%s constructor=warehouse.routing.route_planner."
                    "RoutePlanner.__init__ location_map=%s",
                    action, self.location_map, extra={"row_count": len(self.location_map)},
                )

    def plan_route(self, locations: Sequence[Location]) -> tuple[Location, ...]:
        """Return all known location occurrences in their incoming order."""
        ...
