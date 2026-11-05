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
CONFIG and ORDERS are argument placeholders. CLI parsing, path resolution,
console logging and configuration validation are implemented. Numeric owner
initialization is independently executable but inactive here until Step 07
provides complete order preflight. Output setup and simulation remain deferred.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Mapping, Sequence
from copy import copy
from pathlib import Path

from warehouse.configuration.config_loader import ConfigLoader
from warehouse.configuration.warehouse_config import WarehouseConfig
from warehouse.domain import truck
from warehouse.file_io.activity_log import (
    DIAGNOSTIC_FIELDS, _context_fields, _diagnostic_text, _format_context,
)
from warehouse.workflow import inventory, loading_workflow, order_registry, sequencing_workflow

logger = logging.getLogger("warehouse")


def _initialize_numeric_constants(config: WarehouseConfig) -> None:
    """Coordinate one publication after complete preflight, before any state.

    Step 07 must call this only after the complete order file is validated.
    Earlier owners remain committed if a later owner rejects initialization.
    Successful publication performs no diagnostic I/O.
    """
    inventory.initialize_inventory_constants(config)
    order_registry.initialize_order_constants(config)
    sequencing_workflow.initialize_sequencing_constants(config)
    truck.initialize_truck_constants(config)
    loading_workflow.initialize_loading_constants(config)


def _numeric_initialization_state() -> dict[str, object]:
    """Read actual published bindings for failure evidence without early reads."""
    return {
        f"{module.__name__}.{name}": vars(module).get(name, "unpublished")
        for module in (inventory, order_registry, sequencing_workflow, truck, loading_workflow)
        for name in module.__annotations__
    }


class _CheckedStreamHandler(logging.StreamHandler):
    """Propagate console write/flush failures instead of suppressing them.

    This logging adapter owns no application state. Transport functions do not
    trace themselves, which would recursively emit through the same handler.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            record = copy(record)
            for field in DIAGNOSTIC_FIELDS:
                if field in record.__dict__:
                    record.__dict__[field] = _diagnostic_text(record.__dict__[field])
            text = self.format(record) + self.terminator
            if self.stream.write(text) != len(text):
                raise OSError("console stream did not write the complete diagnostic")
            self.flush()
        except (OSError, ValueError) as error:
            # Preserve type, identity and traceback while distinguishing transport
            # from input errors raised by configuration readers and callbacks.
            error.warehouse_logging_failure = True
            raise


def _below_warning(record: logging.LogRecord) -> bool:
    return record.levelno < logging.WARNING


def _warning_or_higher(record: logging.LogRecord) -> bool:
    return record.levelno >= logging.WARNING


def _configure_console_logging(console_level: int) -> None:
    """Replace this application's console setup without altering root logging."""
    formatter = logging.Formatter(
        "[%(levelname)s] %(message)s "
        + " ".join(f"{field}=%({field})s" for field in DIAGNOSTIC_FIELDS),
        defaults=_context_fields({}),
    )
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
    # Replay setup entry/results only after both destinations are configured.
    for action in ("entry", "returned"):
        logger.debug(
            "function=launcher._configure_console_logging action=%s "
            "console_level=%s handler_count=%s propagate=%s",
            action, logging.getLevelName(console_level), len(logger.handlers), logger.propagate,
        )


def _report_logging_failure(
    error: OSError | ValueError, context: Mapping[str, object]
) -> None:
    """Attempt direct stderr reporting even when the configured transport fails."""
    context = {**context, "numeric_initialization": _numeric_initialization_state()}
    text = (
        "[ERROR] function=launcher.warehouse action=aborted result=INCOMPLETE "
        f"reason=console_logging_failed error={str(error)!r} "
        "last_confirmed_trace_line=n/a "
        f"{_format_context(_context_fields(context))}\n"
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
    """Resolve CLI paths and validate configuration before later startup owners.

    Configure logging before the deferred preflight and numeric initialization.
    Preserve checked INFO command mirrors in both modes and use direct stderr
    fallback if logging fails. Until runtime execution is implemented, return a
    nonzero result rather than reporting a successful simulation.
    """
    arguments = _parse_arguments(argv)
    console_level = logging.DEBUG if arguments.debug == "T" else logging.INFO
    context: dict[str, object] = {
        "argv": tuple(argv), "config_supplied": arguments.config,
        "orders_supplied": arguments.orders, "config_resolved": "n/a",
        "orders_resolved": "n/a", "working_directory": "n/a",
        "debug": arguments.debug, "console_level": logging.getLevelName(console_level),
    }
    try:
        _configure_console_logging(console_level)
    except (OSError, ValueError) as error:
        _report_logging_failure(error, context)
        return 1
    # Step 07 must validate complete order grammar before calling the numeric
    # coordinator here. Config validation or a stub OrderReader is insufficient.
    # TODO (step 08): Create the unique run folder before simulation.
    # TODO (steps 11, 20-23): Construct state/dependencies, run and publish results.
    try:
        working_directory = Path.cwd()
        context["working_directory"] = working_directory
        config_path = (working_directory / arguments.config).resolve()
        context["config_resolved"] = config_path
        orders_path = (working_directory / arguments.orders).resolve()
        context["orders_resolved"] = orders_path
        # Report startup entry/results only after successful parsing permits setup.
        # Help and argument errors must never configure logging or emit diagnostics.
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "function=launcher.warehouse action=entry %s",
                _format_context(context),
            )
            logger.debug(
                "function=launcher._parse_arguments action=entry %s",
                _format_context(context),
            )
            logger.debug(
                "function=launcher._parse_arguments action=returned %s",
                _format_context(context),
            )
        config = ConfigLoader(working_directory).load_config(config_path)
    except (OSError, ValueError, RuntimeError) as error:
        context["numeric_initialization"] = _numeric_initialization_state()
        if getattr(error, "warehouse_logging_failure", False):
            _report_logging_failure(error, context)
            return 1
        try:
            reason = "startup_invariant_failed" if isinstance(error, RuntimeError) else "configuration_failed"
            logger.error(
                "function=launcher.warehouse action=aborted result=INCOMPLETE "
                "reason=%s error=%r; config supplied=%r resolved=%r; "
                "orders supplied=%r resolved=%r; last_confirmed_trace_line=n/a %s",
                reason, str(error), arguments.config, str(context["config_resolved"]),
                arguments.orders, str(context["orders_resolved"]), _format_context(context),
            )
        except (OSError, ValueError) as transport_error:
            _report_logging_failure(transport_error, context)
        return 1
    try:
        logger.error(
            "function=launcher.warehouse action=aborted "
            "configuration validated; simulation startup is not implemented; "
            "reason=pending_order_preflight pending_numeric_integration=06 deferred_steps=07-23; "
            "config supplied=%r, resolved=%r; orders supplied=%r, resolved=%r "
            "result=INCOMPLETE last_confirmed_trace_line=n/a %s",
            arguments.config, str(config_path), arguments.orders, str(orders_path),
            _format_context(context),
        )
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "function=launcher.warehouse action=returned exit_code=1 "
                "validated_locations=%s %s", len(config.location_map), _format_context(context),
            )
    except (OSError, ValueError) as error:
        _report_logging_failure(error, context)
    return 1
