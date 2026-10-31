"""Declare the checked operational trace and shared console boundary.

Only record_command emits INFO. Serialize each accepted command once, flush
"[INFO] " plus that exact text to the checked console, then append and flush
the same command to log.txt. INFO adds no function name, timestamp or context.
DEBUG diagnostics expand full request, order, truck and evidence details while
the trace retains canonical tokens and exact arity. Feedback accepts only
DEBUG, WARNING and ERROR; debug mode T enables all DEBUG details, while F or
omission suppresses them. Checked INFO mirrors remain identical in both modes.
Enabled DEBUG and INFO use stdout; WARNING/ERROR use stderr without duplicates.
Transport failures stop further commands; logging observes completed commits
without recursively tracing its own emit/format functions.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TextIO

if TYPE_CHECKING:
    from warehouse.workflow.warehouse_application import Command

# TODO: Preserve command-only INFO, filter DEBUG and propagate checked I/O errors.


class ActivityLog:
    """Own the sole INFO command path and DEBUG/WARNING/ERROR diagnostic transport."""

    run_directory: Path
    console: TextIO  # Checked stdout stream for exact INFO command mirrors.

    def __init__(self, run_directory: Path, console: TextIO) -> None:
        """Retain the run destination and checked stream used for command mirrors."""
        ...

    def record_command(self, command: Command, context: Mapping[str, object]) -> None:
        """Serialize once; flush [INFO] plus the command before its raw trace append.

        Include one space after [INFO] and a newline after the command. Keep
        function names, IDs and evidence from context in DEBUG diagnostics.
        Emit only after the command commits. A failed mirror adds no trace
        line; a failed append/flush stops work at the last confirmed line.
        """
        ...

    def feedback(
        self,
        level: Literal["DEBUG", "WARNING", "ERROR"],
        function: str,
        message: str,
        context: Mapping[str, object],
    ) -> None:
        """Declare DEBUG/WARNING/ERROR feedback and reject INFO before output.

        Retain qualified function names and complete available context. Filter
        DEBUG at the launcher's selected threshold; never recursively trace
        diagnostic transport or emit a command mirror from this method.
        """
        ...

    def mark_incomplete(self, reason: str, context: Mapping[str, object]) -> None:
        """Report INCOMPLETE at ERROR with the last confirmed trace context."""
        ...
