"""Declare exclusive run-folder creation and final-result publication.

Folder naming uses an aware local start time and retries only name collisions.
Final contents come from canonical loaded trucks and a defensive inventory
snapshot. Publication must stage all contents and clean failed final artifacts
where possible while preserving the diagnostic trace.

Selected entry traces, chosen absolute paths, row counts and successful final
publication use DEBUG under --debug T. Folder collisions use WARNING and I/O
failures use ERROR; this owner emits no INFO command or completion records.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

from warehouse.domain.location import Location
from warehouse.domain.truck import Truck

logger = logging.getLogger("warehouse")

# TODO: Stage complete results and report retained incomplete artifacts on failure.


class OutputWriter:
    """Own output effects without keeping a duplicate warehouse business archive."""

    def create_output_directory(
        self, working_directory: Path, started_at: datetime
    ) -> Path:
        """Create an exclusive local-time run folder with numeric collision suffixes.

        Report name collisions at WARNING and the chosen absolute path at DEBUG.
        """
        if started_at.utcoffset() is None:
            raise ValueError("started_at must be an aware datetime")
        parent = working_directory.resolve()
        name = started_at.astimezone().strftime("warehouse_%Y%m%d%H%M")
        candidate = parent / name
        suffix = 0
        created = False
        try:
            while True:
                try:
                    candidate.mkdir(exist_ok=False)
                except FileExistsError:
                    suffix += 1
                    next_path = parent / f"{name}_{suffix:02d}"
                    logger.warning(
                        "function=output_writer.create_output_directory action=collision "
                        "next_path=%s", next_path, extra={"path": candidate},
                    )
                    candidate = next_path
                else:
                    created = True
                    logger.debug(
                        "function=output_writer.create_output_directory action=created",
                        extra={"path": candidate},
                    )
                    return candidate
        except (OSError, ValueError) as error:
            # Keep the original OS/transport failure and facts that cannot be
            # recovered by the launcher when creation succeeds before logging fails.
            error.warehouse_output_path = candidate
            if created:
                error.warehouse_run_directory = candidate
            raise

    def write_orders(self, output_directory: Path, trucks: Sequence[Truck]) -> None:
        """Publish one manifest per loaded truck, including a retained partial truck."""
        ...

    def write_inventory(
        self, output_directory: Path, quantities: Mapping[Location, int]
    ) -> None:
        """Publish every mapped stock quantity with canonical headers in traversal order."""
        ...
