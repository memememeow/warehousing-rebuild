"""Declare the checked operational trace and shared console boundary.

Every accepted command must be mirrored and flushed before trace append and
flush. Diagnostics expand request, order, truck and evidence context while the
trace retains canonical tokens and exact arity. Transport failures stop further
commands; logging must observe completed commits without recursive tracing.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TextIO

if TYPE_CHECKING:
    from warehouse.workflow.warehouse_application import Command

# TODO: Configure severity streams before preflight and propagate checked I/O errors.


class ActivityLog:
    """Own trace serialization and contextual feedback without mutating business state."""

    run_directory: Path
    console: TextIO

    def __init__(self, run_directory: Path, console: TextIO) -> None:
        """Retain the run destination and checked stream used for command mirrors."""
        ...

    def record_command(self, command: Command, context: Mapping[str, object]) -> None:
        """Flush the exact contextual mirror before appending an accepted trace line."""
        ...

    def feedback(
        self,
        level: Literal["INFO", "WARNING", "ERROR"],
        function: str,
        message: str,
        context: Mapping[str, object],
    ) -> None:
        """Emit qualified diagnostic context at the requested severity without recursion."""
        ...

    def mark_incomplete(self, reason: str, context: Mapping[str, object]) -> None:
        """Report an incomplete run while retaining the last confirmed trace context."""
        ...
