"""Declare deterministic scheduling and typed internal command contracts.

The application routes shared requests between their owners, finishes input
once and drains work before publishing results. It holds explicit dependencies
without duplicating business collections. Commands retain SKU evidence that
the operational trace grammar omits.

After each accepted operational command commits, delegate its sole INFO mirror
and raw trace append to ActivityLog.record_command. Function entry/results,
handoffs, lifecycle changes and completion details use DEBUG under --debug T;
F or omission suppresses those details without changing commands or outputs.
Buffer nested diagnostics until related in-memory updates complete. No debug
setting belongs in application properties or the Command aliases.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from warehouse.domain.location import Location
from warehouse.domain.warehouse_state import WarehouseState
from warehouse.domain.worker_type import WorkerType
from warehouse.file_io.activity_log import ActivityLog
from warehouse.file_io.order_reader import OrderLine
from warehouse.file_io.output_writer import OutputWriter
from warehouse.routing.route_planner import RoutePlanner

# TODO: Connect sequential owner calls, blocked-work diagnostics and publication.

type OrderCommand = tuple[Literal["Order"], str, str]
type ReadyCommand = tuple[WorkerType, str, Literal["ready"]]
type PickingCommand = tuple[Literal[WorkerType.PICKER], str, Literal["pick"], int, str]
type MarshallingCommand = tuple[
    Literal[WorkerType.PICKER], str, Literal["to"], Literal["Marshalling"]
]
type SequencingCommand = tuple[
    Literal[WorkerType.SEQUENCER], str, Literal["sequence"], int, tuple[str, ...]
]
type RescanCommand = tuple[
    Literal[WorkerType.SEQUENCER, WorkerType.LOADER],
    str,
    Literal["rescan"],
    int,
    tuple[str, ...],
]
type LoadingCommand = tuple[
    Literal[WorkerType.LOADER], str, Literal["load"], int, tuple[str, ...]
]
type ReplenishmentCommand = tuple[
    Literal[WorkerType.REPLENISHER], str, Literal["replenish"], Location
]
type Command = (
    OrderCommand
    | ReadyCommand
    | PickingCommand
    | MarshallingCommand
    | SequencingCommand
    | RescanCommand
    | LoadingCommand
    | ReplenishmentCommand
)


class WarehouseApplication:
    """Coordinate owners and boundary effects for one sequential simulation."""

    state: WarehouseState
    planner: RoutePlanner
    files: OutputWriter
    log: ActivityLog
    output_directory: Path

    def __init__(
        self,
        state: WarehouseState,
        planner: RoutePlanner,
        files: OutputWriter,
        log: ActivityLog,
        output_directory: Path,
    ) -> None:
        """Retain shared state and explicit routing, logging and output dependencies."""
        ...

    def run(self, orders: Sequence[OrderLine]) -> None:
        """Process full batches sequentially, close input and drain before publication."""
        ...

    def apply_command(self, command: Command) -> None:
        """Delegate validated actions, then call ActivityLog.record_command.

        Complete related in-memory handoffs before flushing buffered DEBUG
        details and the accepted command's INFO mirror and trace append.
        Recoverable rejections use WARNING; application code emits no INFO.
        """
        ...
