"""Declare FIFO sequencing, staged SKU evidence and the separate rescan.

Assignments retain a canonical request and optional staged evidence. Rescans
compare both observations with N fronts followed by N backs. Content failure
clears the assignment and returns the same request for picking rework.

FIFO assignments, staged/rescanned SKU evidence and committed status/rework
details use DEBUG under --debug T.
Preserve WARNING/ERROR for problems and buffer nested diagnostics until related
updates finish. Emit no INFO outside ActivityLog.record_command.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from typing import Final

from warehouse.configuration.warehouse_config import WarehouseConfig
from warehouse.domain.fulfillment_request import FulfillmentRequest

PALLET_CAPACITY_FASCIA: Final[int]

type SequencingAssignment = tuple[FulfillmentRequest, tuple[str, ...] | None]


def initialize_sequencing_constants(config: WarehouseConfig) -> None:
    """Publish the validated pallet capacity once before state exists."""
    global PALLET_CAPACITY_FASCIA

    if "PALLET_CAPACITY_FASCIA" in globals():
        raise RuntimeError("sequencing_workflow numeric constants already initialized")

    PALLET_CAPACITY_FASCIA = config.pallet_capacity_fascia


def __getattr__(name: str) -> object:
    """Reject numeric reads before explicit startup publication."""
    if name == "PALLET_CAPACITY_FASCIA":
        raise RuntimeError(f"sequencing_workflow.{name} read before numeric initialization")

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Step 18 owns pallet calculations and evidence preservation on invalid commands.
class SequencingWorkflow:
    """Own marshalled work, sequencing assignments and content-validation results."""

    sequencing_queue: deque[FulfillmentRequest]
    sequencer_assignments: dict[str, SequencingAssignment]
    employees: frozenset[str]

    def __init__(self, employees: frozenset[str]) -> None:
        """Establish empty sequencing collections for configured sequencer names."""
        ...

    def enqueue_request(self, request: FulfillmentRequest) -> None:
        """Queue an already-ready canonical request without rewriting its status."""
        ...

    def assign_sequencer(self, worker: str) -> FulfillmentRequest:
        """Assign the oldest ready request and commit SEQUENCING status."""
        ...

    def sequence_request(
        self, worker: str, request_number: int, sku_order: Sequence[str]
    ) -> None:
        """Stage observed SKU order and commit AWAITING_SEQUENCE_RESCAN status."""
        ...

    def rescan(
        self, worker: str, request_number: int, scanned_skus: Sequence[str]
    ) -> tuple[bool, FulfillmentRequest]:
        """Validate staged and rescanned content, returning pass or rework with identity."""
        ...
