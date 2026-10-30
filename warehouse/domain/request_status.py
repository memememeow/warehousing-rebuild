"""Define every permitted Phase 1 fulfillment lifecycle status.

FulfillmentRequest will be the sole status writer. Active workflows must commit
status changes with their authoritative memberships; run failures preserve the
last committed status rather than introducing another request stage.
"""

from __future__ import annotations

from enum import StrEnum

# TODO: Enforce the permitted transition graph in FulfillmentRequest.


class RequestStatus(StrEnum):
    """Name fulfillment stages, including sequencing rework and truck dispatch."""

    CREATED = "created"
    WAITING_FOR_PICKING = "waiting_for_picking"
    PICKING = "picking"
    READY_FOR_SEQUENCING = "ready_for_sequencing"
    SEQUENCING = "sequencing"
    AWAITING_SEQUENCE_RESCAN = "awaiting_sequence_rescan"
    REWORK_REQUIRED = "rework_required"
    READY_FOR_LOADING = "ready_for_loading"
    LOADING = "loading"
    AWAITING_LOAD_RESCAN = "awaiting_load_rescan"
    LOADED = "loaded"
    DISPATCHED = "dispatched"
