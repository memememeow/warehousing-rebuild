"""Declare exclusive run-folder creation and final-result publication.

Folder naming uses an aware local start time and retries only name collisions.
Final contents come from canonical loaded trucks and a defensive inventory
snapshot. Publication must stage all contents and clean failed final artifacts
where possible while preserving the diagnostic trace.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

from warehouse.domain.location import Location
from warehouse.domain.truck import Truck

# TODO: Stage complete results and report retained incomplete artifacts on failure.


class OutputWriter:
    """Own output effects without keeping a duplicate warehouse business archive."""

    def create_output_directory(
        self, working_directory: Path, started_at: datetime
    ) -> Path:
        """Create an exclusive local-time run folder with numeric collision suffixes."""
        ...

    def write_orders(self, output_directory: Path, trucks: Sequence[Truck]) -> None:
        """Publish one manifest per loaded truck, including a retained partial truck."""
        ...

    def write_inventory(
        self, output_directory: Path, quantities: Mapping[Location, int]
    ) -> None:
        """Publish every mapped stock quantity with canonical headers in traversal order."""
        ...
