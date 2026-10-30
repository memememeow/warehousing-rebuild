"""Declare FIFO sequencing, staged SKU evidence and the separate rescan.

Assignments retain a canonical request and optional staged evidence. Rescans
compare both observations with N fronts followed by N backs. Content failure
clears the assignment and returns the same request for picking rework.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from typing import Final

from warehouse.domain.fulfillment_request import FulfillmentRequest

# TODO: Initialize capacity once and preserve evidence on invalid worker or stage.
PALLET_CAPACITY_FASCIA: Final[int]

type SequencingAssignment = tuple[FulfillmentRequest, tuple[str, ...] | None]


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
