"""Declare immutable four-part warehouse addresses and SKU location mappings.

Readers provide canonical zone spelling and nonnegative coordinates. Location
values are hashable inventory keys; the mapping alias preserves traversal order
without introducing another application class.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

# TODO: Enforce coordinate and canonical-zone constraints at input validation.


@dataclass(frozen=True, slots=True)
class Location:
    """Identify one storage address with a canonical zone and three coordinates."""

    zone: str
    aisle: int
    rack: int
    level: int


type LocationMap = Mapping[str, Location]
