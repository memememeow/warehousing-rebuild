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
console logging, complete configuration/order preflight and numeric owner
initialization and exclusive run-folder creation are implemented. State,
application execution, operational trace and final publication remain deferred.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Mapping, Sequence
from copy import copy
from datetime import datetime
from pathlib import Path

from warehouse.configuration.config_loader import ConfigLoader
from warehouse.configuration.warehouse_config import WarehouseConfig
from warehouse.domain import truck
from warehouse.file_io.activity_log import (
    DIAGNOSTIC_FIELDS,
    _context_fields,
    _format_context,
)
from warehouse.file_io.order_reader import OrderReader
from warehouse.file_io.output_writer import OutputWriter
from warehouse.workflow import (
    inventory,
    loading_workflow,
    order_registry,
    sequencing_workflow,
)

logger = logging.getLogger("warehouse")


def warehouse(argv: Sequence[str]) -> int:
    """Preflight inputs, initialize numeric owners once and create the run folder.

    Configure logging before preflight and numeric initialization.
    Preserve checked INFO command mirrors in both modes and use direct stderr
    fallback if logging fails. Until runtime execution is implemented, return a
    nonzero result rather than reporting a successful simulation.
    """
    arguments = _parse_arguments(argv)
    console_level = logging.DEBUG if arguments.debug == "T" else logging.INFO
    context: dict[str, object] = {
        "argv": tuple(argv),
        "config_supplied": arguments.config,
        "orders_supplied": arguments.orders,
        "config_resolved": "n/a",
        "orders_resolved": "n/a",
        "working_directory": "n/a",
        "debug": arguments.debug,
        "console_level": logging.getLevelName(console_level),
    }

    try:
        _configure_console_logging(console_level)
    except (OSError, ValueError) as error:
        _report_logging_failure(error, context)
        return 1

    # TODO (steps 11, 20-23): Construct state/dependencies, run and publish results.
    try:
        # Parsing and transport setup stay silent; help must stop before startup.
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "function=launcher.warehouse action=entry %s",
                _format_context(_context_fields(context, logging.DEBUG)),
            )

        working_directory = Path.cwd()
        context["working_directory"] = working_directory
        started_at = datetime.now().astimezone()

        config_path = (working_directory / arguments.config).resolve()
        context["config_resolved"] = config_path
        orders_path = (working_directory / arguments.orders).resolve()
        context["orders_resolved"] = orders_path

        config = ConfigLoader(working_directory).load_config(config_path)
    except (OSError, ValueError, RuntimeError) as error:
        _report_startup_failure(error, "configuration_failed", context)
        return 1

    try:
        orders = OrderReader().read_orders(orders_path)
    except (OSError, ValueError, RuntimeError) as error:
        _report_startup_failure(error, "order_preflight_failed", context)
        return 1

    try:
        _initialize_numeric_constants(config)
    except (OSError, ValueError, RuntimeError) as error:
        # Keep the existing preflight reason for non-invariant initializer errors.
        _report_startup_failure(error, "order_preflight_failed", context)
        return 1

    try:
        files = OutputWriter()
        output_directory = files.create_output_directory(working_directory, started_at)
        context["run_directory"] = output_directory
    except (OSError, ValueError, RuntimeError) as error:
        _report_startup_failure(error, "run_folder_failed", context)
        return 1

    # Retain orders for run (20), files/output_directory for application
    # construction (20), and the selected destination for ActivityLog (21).
    # started_at remains the aware run-start evidence; later request times
    # must be captured freshly. State construction belongs to Step 11.
    _report_pending_simulation(len(orders), context)
    return 1


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


def _initialize_numeric_constants(config: WarehouseConfig) -> None:
    """Coordinate one publication after complete preflight, before any state.

    Call this only after the complete order file is validated.
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


def _report_startup_failure(
    error: OSError | ValueError | RuntimeError,
    default_reason: str,
    context: dict[str, object],
) -> None:
    """Report the boundary's default reason and retain published owner state."""
    if hasattr(error, "warehouse_run_directory"):
        context["run_directory"] = error.warehouse_run_directory
    elif hasattr(error, "warehouse_output_path"):
        context["path"] = error.warehouse_output_path
    context["numeric_initialization"] = _numeric_initialization_state()

    if getattr(error, "warehouse_logging_failure", False):
        _report_logging_failure(error, context)
        return

    reason = "startup_invariant_failed" if isinstance(error, RuntimeError) else default_reason
    # Input paths and standard diagnostic fields are emitted separately below.
    failure_context = {
        key: value for key, value in context.items()
        if key not in (
            "argv", "config_supplied", "config_resolved",
            "orders_supplied", "orders_resolved", *DIAGNOSTIC_FIELDS,
        )
    }

    try:
        logger.error(
            "function=launcher.warehouse action=aborted result=INCOMPLETE "
            "reason=%s error=%r; config supplied=%r resolved=%r; "
            "orders supplied=%r resolved=%r; last_confirmed_trace_line=n/a %s",
            reason, str(error), context["config_supplied"], str(context["config_resolved"]),
            context["orders_supplied"], str(context["orders_resolved"]),
            _format_context(failure_context),
            extra={key: context[key] for key in DIAGNOSTIC_FIELDS if key in context},
        )
    except (OSError, ValueError) as transport_error:
        _report_logging_failure(transport_error, {
            **context, "startup_failure": reason, "startup_error": error,
        })


def _report_pending_simulation(order_count: int, context: Mapping[str, object]) -> None:
    """Keep validated startup distinct from the deferred simulation runtime."""
    try:
        logger.error(
            "function=launcher.warehouse action=aborted "
            "configuration validated; complete order preflight and numeric initialization validated; "
            "simulation startup is not implemented; "
            "reason=pending_simulation deferred_steps=09-23 order_count=%d; "
            "config supplied=%r, resolved=%r; orders supplied=%r, resolved=%r "
            "result=INCOMPLETE last_confirmed_trace_line=n/a",
            order_count, context["config_supplied"], str(context["config_resolved"]),
            context["orders_supplied"], str(context["orders_resolved"]),
            extra={"run_directory": context["run_directory"]},
        )
    except (OSError, ValueError) as error:
        _report_logging_failure(error, context)


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


def _below_warning(record: logging.LogRecord) -> bool:
    return record.levelno < logging.WARNING


def _warning_or_higher(record: logging.LogRecord) -> bool:
    return record.levelno >= logging.WARNING


class _CheckedStreamHandler(logging.StreamHandler):
    """Propagate console write/flush failures instead of suppressing them.

    This logging adapter owns no application state. Transport functions do not
    trace themselves, which would recursively emit through the same handler.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            record = copy(record)
            fields = _context_fields({
                field: record.__dict__[field] for field in DIAGNOSTIC_FIELDS
                if field in record.__dict__
            }, record.levelno)
            context = _format_context(fields) if record.levelno != logging.INFO else ""

            text = self.format(record) + (f" {context}" if context else "") + self.terminator
            if self.stream.write(text) != len(text):
                raise OSError("console stream did not write the complete diagnostic")
            self.flush()
        except (OSError, ValueError) as error:
            # Preserve type, identity and traceback while distinguishing transport
            # from input errors raised by configuration readers and callbacks.
            error.warehouse_logging_failure = True
            raise
