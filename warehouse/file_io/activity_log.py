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
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, TextIO

if TYPE_CHECKING:
    from warehouse.workflow.warehouse_application import Command

logger = logging.getLogger("warehouse")

DIAGNOSTIC_FIELDS: Final[tuple[str, ...]] = (
    "run_directory", "request_id", "request_sequence", "order_ids",
    "truck_id", "worker_type", "worker", "source", "line_number",
    "path", "row_count", "sku_evidence", "status_before", "status_after",
    "location", "stock_before", "stock_after",
)


def _context_fields(context: Mapping[str, object]) -> dict[str, object]:
    """Fill unavailable diagnostic fields without mutating the caller's evidence."""
    fields: dict[str, object] = dict.fromkeys(DIAGNOSTIC_FIELDS, "n/a")
    fields.update({name: value if value is not None else "n/a" for name, value in context.items()})
    return fields


def _format_context(context: Mapping[str, object]) -> str:
    """Expand diagnostic evidence without abbreviating collections of IDs."""
    return " ".join(
        f"{name}={_diagnostic_text(value) if value is not None else 'n/a'}"
        for name, value in context.items()
    )


def _diagnostic_text(value: object, *, conversion: Literal["s", "r"] = "s") -> str:
    """Render complete numeric evidence without Python's decimal conversion cap.

    This is recursive diagnostic transport, so it must not log through itself.
    Strings retain the caller's str/repr convention; containers quote their items.
    """
    if type(value) is int:
        magnitude = abs(value)
        chunks: list[int] = []
        while magnitude >= 1_000_000_000:
            magnitude, remainder = divmod(magnitude, 1_000_000_000)
            chunks.append(remainder)
        return ("-" if value < 0 else "") + str(magnitude) + "".join(
            f"{chunk:09d}" for chunk in reversed(chunks)
        )
    if isinstance(value, Mapping):
        return "{" + ", ".join(
            f"{_diagnostic_text(key, conversion='r')}: {_diagnostic_text(item, conversion='r')}"
            for key, item in value.items()
        ) + "}"
    if isinstance(value, (tuple, list)):
        items = ", ".join(_diagnostic_text(item, conversion="r") for item in value)
        if isinstance(value, tuple):
            return "(" + items + ("," if len(value) == 1 else "") + ")"
        return "[" + items + "]"
    if is_dataclass(value) and not isinstance(value, type):
        return type(value).__qualname__ + "(" + ", ".join(
            f"{field.name}={_diagnostic_text(getattr(value, field.name), conversion='r')}"
            for field in fields(value) if field.repr
        ) + ")"
    return repr(value) if conversion == "r" else str(value)


# TODO (step 21): Serialize checked INFO mirrors and track confirmed trace appends.


class ActivityLog:
    """Own the sole INFO command path and DEBUG/WARNING/ERROR diagnostic transport."""

    run_directory: Path
    console: TextIO  # Checked stdout stream for exact INFO command mirrors.

    def __init__(self, run_directory: Path, console: TextIO) -> None:
        """Retain the run destination and checked stream used for command mirrors."""
        logger.debug(
            "function=activity_log.__init__ action=entry",
            extra={"run_directory": run_directory},
        )
        self.run_directory = run_directory
        self.console = console
        self.feedback(
            "DEBUG", "activity_log.__init__",
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

        Use file/function labels and retain complete available context. Filter
        DEBUG at the launcher's selected threshold; never recursively trace
        diagnostic transport or emit a command mirror from this method.
        """
        if level not in ("DEBUG", "WARNING", "ERROR"):
            raise ValueError("feedback level must be DEBUG, WARNING or ERROR; INFO is reserved")
        severity = getattr(logging, level)
        if logger.isEnabledFor(severity):
            fields = _context_fields({"run_directory": self.run_directory, **context})
            logger.log(
                severity, "function=%s %s %s", function, message,
                _format_context({
                    name: value for name, value in fields.items()
                    if name not in DIAGNOSTIC_FIELDS
                }),
                extra={name: fields[name] for name in DIAGNOSTIC_FIELDS},
            )

    def mark_incomplete(self, reason: str, context: Mapping[str, object]) -> None:
        """Report INCOMPLETE at ERROR with the last confirmed trace context."""
        self.feedback(
            "DEBUG", "activity_log.mark_incomplete",
            f"action=entry reason={reason!r}", context,
        )
        self.feedback(
            "ERROR", "activity_log.mark_incomplete",
            f"action=aborted result=INCOMPLETE reason={reason!r}",
            {"last_confirmed_trace_line": "n/a", **context},
        )
        self.feedback(
            "DEBUG", "activity_log.mark_incomplete",
            f"action=returned result=INCOMPLETE reason={reason!r}", context,
        )
