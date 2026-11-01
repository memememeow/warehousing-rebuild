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

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TextIO

if TYPE_CHECKING:
    from warehouse.workflow.warehouse_application import Command

logger = logging.getLogger("warehouse")


def _format_context(context: Mapping[str, object]) -> str:
    """Expand diagnostic evidence without abbreviating collections of IDs."""
    fields: dict[str, object] = dict.fromkeys(
        (
            "run_directory", "request_id", "request_sequence", "order_ids",
            "truck_id", "worker_type", "worker", "source", "line_number",
            "path", "row_count", "sku_evidence", "status_before", "status_after",
            "location", "stock_before", "stock_after",
        ),
        "n/a",
    )
    fields.update(context)
    return " ".join(
        f"{name}={value if value is not None else 'n/a'}"
        for name, value in fields.items()
    )


# TODO (step 21): Serialize checked INFO mirrors and track confirmed trace appends.


class ActivityLog:
    """Own the sole INFO command path and DEBUG/WARNING/ERROR diagnostic transport."""

    run_directory: Path
    console: TextIO  # Checked stdout stream for exact INFO command mirrors.

    def __init__(self, run_directory: Path, console: TextIO) -> None:
        """Retain the run destination and checked stream used for command mirrors."""
        logger.debug(
            "function=warehouse.file_io.activity_log.ActivityLog.__init__ action=entry %s",
            _format_context({"run_directory": run_directory}),
        )
        self.run_directory = run_directory
        self.console = console
        self.feedback(
            "DEBUG", "warehouse.file_io.activity_log.ActivityLog.__init__",
            "action=returned", {},
        )

    def record_command(self, command: Command, context: Mapping[str, object]) -> None:
        """Serialize once; flush [INFO] plus the command before its raw trace append.

        Include one space after [INFO] and a newline after the command. Keep
        function names, IDs and evidence from context in DEBUG diagnostics.
        Emit only after the command commits. A failed mirror adds no trace
        line; a failed append/flush stops work at the last confirmed line.
        """
        # Step 21 owns command serialization, checked mirroring and trace I/O.
        ...

    def feedback(
        self,
        level: Literal["DEBUG", "WARNING", "ERROR"],
        function: str,
        message: str,
        context: Mapping[str, object],
    ) -> None:
        """Emit DEBUG/WARNING/ERROR feedback and reject INFO before output.

        Retain qualified function names and complete available context. Filter
        DEBUG at the launcher's selected threshold; never recursively trace
        diagnostic transport or emit a command mirror from this method.
        """
        if level not in ("DEBUG", "WARNING", "ERROR"):
            raise ValueError("feedback level must be DEBUG, WARNING or ERROR; INFO is reserved")
        severity = getattr(logging, level)
        if logger.isEnabledFor(severity):
            logger.log(
                severity, "function=%s %s %s", function, message,
                _format_context({"run_directory": self.run_directory, **context}),
            )

    def mark_incomplete(self, reason: str, context: Mapping[str, object]) -> None:
        """Report INCOMPLETE at ERROR with the last confirmed trace context."""
        self.feedback(
            "ERROR", "warehouse.file_io.activity_log.ActivityLog.mark_incomplete",
            f"action=aborted result=INCOMPLETE reason={reason!r}",
            {"last_confirmed_trace_line": "n/a", **context},
        )
