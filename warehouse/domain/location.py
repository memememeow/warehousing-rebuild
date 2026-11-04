"""Provide immutable four-part warehouse addresses and SKU location mappings.

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

from warehouse.file_io.activity_log import _diagnostic_text

logger = logging.getLogger("warehouse")


@dataclass(frozen=True, slots=True)
class Location:
    """Identify one storage address with a canonical zone and three coordinates."""

    zone: str
    aisle: int
    rack: int
    level: int

    def __post_init__(self) -> None:
        """Validate coordinates; the reader owns canonical zones and membership."""
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "function=location.__post_init__ action=entry "
                "constructor=location.__init__ "
                "zone=%s aisle=%s rack=%s level=%s",
                self.zone, _diagnostic_text(self.aisle), _diagnostic_text(self.rack),
                _diagnostic_text(self.level),
                extra={"location": (self.zone, self.aisle, self.rack, self.level)},
            )
        for name, value in (("aisle", self.aisle), ("rack", self.rack), ("level", self.level)):
            if type(value) is not int or value < 0:
                raise ValueError(
                    f"Location {name} must be a nonnegative integer: "
                    f"{_diagnostic_text(value, conversion='r')}"
                )
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "function=location.__post_init__ action=returned "
                "constructor=location.__init__ "
                "zone=%s aisle=%s rack=%s level=%s",
                self.zone, _diagnostic_text(self.aisle), _diagnostic_text(self.rack),
                _diagnostic_text(self.level),
                extra={"location": (self.zone, self.aisle, self.rack, self.level)},
            )


type LocationMap = Mapping[str, Location]
