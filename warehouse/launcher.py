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
CONFIG and ORDERS are argument placeholders. CLI parsing and path resolution
are implemented; simulation startup and execution remain deferred.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path


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

    Later steps configure logging before preflight and initialize numeric settings.
    Preserve checked INFO command mirrors in both modes and use direct stderr
    fallback if logging fails. Until runtime execution is implemented, return a
    nonzero result rather than reporting a successful simulation.
    """
    arguments = _parse_arguments(argv)
    working_directory = Path.cwd()
    config_path = (working_directory / arguments.config).resolve()
    orders_path = (working_directory / arguments.orders).resolve()

    # TODO (step 02): Configure console logging before preflight reads.
    # TODO (steps 03-07): Complete preflight, then initialize owner constants.
    # TODO (step 08): Create the unique run folder before simulation.
    # TODO (steps 11, 20-23): Construct state/dependencies, run and publish results.
    print(
        "[ERROR] warehouse.launcher.warehouse: simulation startup is not implemented; "
        f"config supplied={arguments.config!r}, resolved={str(config_path)!r}; "
        f"orders supplied={arguments.orders!r}, resolved={str(orders_path)!r}",
        file=sys.stderr,
    )
    return 1
