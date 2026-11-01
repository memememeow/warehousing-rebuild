"""Own startup, dependency construction and process exit handling.

Preflight must finish before mutable state exists. The launcher captures the
working directory, initializes validated constants once and constructs shared
dependencies. Fatal failures must produce a nonzero result.

Parse required --config and --orders paths plus optional --debug {T,F}, default
F. Reject missing option values and tokens other than uppercase T/F; --help exits
before state or output exists. Configure the shared logger and handlers before
reading files: T selects DEBUG, while F or omission selects INFO. Compare the
token with "T" rather than converting its string to bool. Enabled DEBUG details
and checked INFO command mirrors use stdout; WARNING/ERROR use stderr without
duplicates. Only ActivityLog.record_command emits INFO; startup settings and
completion details use DEBUG. Keep debug mode outside configuration and state.

Planned run command: python3.15 warehouse --config CONFIG --orders ORDERS --debug T
Omitting --debug or supplying --debug F suppresses DEBUG details only.
Planned usage command: python3.15 warehouse --help
CONFIG and ORDERS are argument placeholders. CLI parsing, path resolution and
console logging are implemented; preflight and simulation remain deferred.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path


logger = logging.getLogger("warehouse")


class _CheckedStreamHandler(logging.StreamHandler):
    """Propagate console write/flush failures instead of suppressing them.

    This logging adapter owns no application state. Transport functions do not
    trace themselves, which would recursively emit through the same handler.
    """

    def emit(self, record: logging.LogRecord) -> None:
        text = self.format(record) + self.terminator
        if self.stream.write(text) != len(text):
            raise OSError("console stream did not write the complete diagnostic")
        self.flush()


def _below_warning(record: logging.LogRecord) -> bool:
    return record.levelno < logging.WARNING


def _warning_or_higher(record: logging.LogRecord) -> bool:
    return record.levelno >= logging.WARNING


def _configure_console_logging(console_level: int) -> None:
    """Replace this application's console setup without altering root logging."""
    formatter = logging.Formatter("[%(levelname)s] %(message)s")
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    for stream, severity_filter in (
        (sys.stdout, _below_warning),
        (sys.stderr, _warning_or_higher),
    ):
        handler = _CheckedStreamHandler(stream)
        handler.setLevel(console_level)
        handler.setFormatter(formatter)
        handler.addFilter(severity_filter)
        logger.addHandler(handler)
    logger.setLevel(console_level)
    logger.disabled = False
    logger.propagate = False


def _report_logging_failure(error: OSError | ValueError) -> None:
    """Attempt direct stderr reporting even when the configured transport fails."""
    text = (
        "[ERROR] function=warehouse.launcher.warehouse result=INCOMPLETE "
        f"reason=console_logging_failed error={str(error)!r} "
        "last_confirmed_trace_line=n/a request_id=n/a request_sequence=n/a "
        "order_ids=n/a truck_id=n/a\n"
    )
    try:
        sys.stderr.write(text)
        sys.stderr.flush()
    except (OSError, ValueError):
        # No functioning error stream remains; the nonzero exit still signals failure.
        pass


def _parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    """Declare required paths and optional --debug choices T/F with default F.

    Use choices=("T", "F") and default="F"; require a value when supplied.
    Help documents the option and default. Missing or invalid debug values fail
    before reading files, creating output or constructing mutable state.
    """
    parser = argparse.ArgumentParser(prog="warehouse")
    parser.add_argument(
        "--config", required=True, metavar="PATH", help="Warehouse configuration path."
    )
    parser.add_argument(
        "--orders", required=True, metavar="PATH", help="Customer orders path."
    )
    parser.add_argument(
        "--debug",
        choices=("T", "F"),
        default="F",
        help="Print DEBUG diagnostics: T enables; F disables (default: F).",
    )
    return parser.parse_args(argv)


def warehouse(argv: Sequence[str]) -> int:
    """Parse CLI paths without reading inputs or starting the simulation.

    Configure logging before the deferred preflight and numeric initialization.
    Preserve checked INFO command mirrors in both modes and use direct stderr
    fallback if logging fails. Until runtime execution is implemented, return a
    nonzero result rather than reporting a successful simulation.
    """
    arguments = _parse_arguments(argv)
    console_level = logging.DEBUG if arguments.debug == "T" else logging.INFO
    _configure_console_logging(console_level)
    working_directory = Path.cwd()
    config_path = (working_directory / arguments.config).resolve()
    orders_path = (working_directory / arguments.orders).resolve()

    # TODO (steps 03-07): Complete preflight, then initialize owner constants.
    # TODO (step 08): Create the unique run folder before simulation.
    # TODO (steps 11, 20-23): Construct state/dependencies, run and publish results.
    try:
        # Report startup entry/results only after successful parsing permits setup.
        # Help and argument errors must never configure logging or emit diagnostics.
        logger.debug(
            "function=warehouse.launcher.warehouse action=entry argv=%r "
            "working_directory=%s request_id=n/a request_sequence=n/a "
            "order_ids=n/a truck_id=n/a",
            list(argv), working_directory,
        )
        logger.debug(
            "function=warehouse.launcher._parse_arguments action=entry argv=%r "
            "request_id=n/a request_sequence=n/a order_ids=n/a truck_id=n/a",
            list(argv),
        )
        logger.debug(
            "function=warehouse.launcher._parse_arguments action=returned "
            "config=%r orders=%r debug=%s console_level=%s "
            "request_id=n/a request_sequence=n/a order_ids=n/a truck_id=n/a",
            arguments.config, arguments.orders, arguments.debug,
            logging.getLevelName(console_level),
        )
        logger.error(
            "function=warehouse.launcher.warehouse action=aborted "
            "simulation startup is not implemented; "
            "config supplied=%r, resolved=%r; orders supplied=%r, resolved=%r "
            "result=INCOMPLETE last_confirmed_trace_line=n/a "
            "request_id=n/a request_sequence=n/a order_ids=n/a truck_id=n/a",
            arguments.config, str(config_path), arguments.orders, str(orders_path),
        )
        logger.debug(
            "function=warehouse.launcher.warehouse action=returned exit_code=1 "
            "config_path=%s orders_path=%s request_id=n/a request_sequence=n/a "
            "order_ids=n/a truck_id=n/a",
            config_path, orders_path,
        )
    except (OSError, ValueError) as error:
        _report_logging_failure(error)
    return 1
