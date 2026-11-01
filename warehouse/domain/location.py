"""Declare immutable four-part warehouse addresses and SKU location mappings.

Readers provide canonical zone spelling and nonnegative coordinates. Location
values are hashable inventory keys; the mapping alias preserves traversal order
without introducing another application class.

Construction and location-value diagnostics use DEBUG under --debug T, without
adding logging settings to the immutable address or emitting INFO here.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass

logger = logging.getLogger("warehouse")

# TODO: Enforce coordinate and canonical-zone constraints at input validation.


@dataclass(frozen=True, slots=True)
class Location:
    """Identify one storage address with a canonical zone and three coordinates."""

    zone: str
    aisle: int
    rack: int
    level: int

    def __post_init__(self) -> None:
        """Trace generated construction after all immutable fields are assigned."""
        for action in ("entry", "returned"):
            logger.debug(
                "function=warehouse.domain.location.Location.__post_init__ action=%s "
                "constructor=warehouse.domain.location.Location.__init__ "
                "zone=%s aisle=%s rack=%s level=%s",
                action, self.zone, self.aisle, self.rack, self.level,
                extra={"location": (self.zone, self.aisle, self.rack, self.level)},
            )


type LocationMap = Mapping[str, Location]
