"""Provide immutable four-part warehouse addresses and SKU location mappings.

Readers provide canonical zone spelling and nonnegative coordinates. Location
values are hashable inventory keys; the mapping alias preserves traversal order
without introducing another application class.

Construction stays silent. Validation errors retain the coordinate and observed
value; this immutable address has no logging setting or INFO events.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from warehouse.file_io.activity_log import _diagnostic_text


@dataclass(frozen=True, slots=True)
class Location:
    """Identify one storage address with a canonical zone and three coordinates."""

    zone: str
    aisle: int
    rack: int
    level: int

    def __post_init__(self) -> None:
        """Validate coordinates; the reader owns canonical zones and membership."""
        for name, value in (
            ("aisle", self.aisle), ("rack", self.rack), ("level", self.level),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(
                    f"Location {name} must be a nonnegative integer: "
                    f"{_diagnostic_text(value, conversion='r')}"
                )


type LocationMap = Mapping[str, Location]
