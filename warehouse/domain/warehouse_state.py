"""Declare the shared lifetime owner of mutable warehouse components.

The state composes the registry, inventory and three workflow owners after
preflight. Each owner retains its own authoritative collections, while
replenisher readiness remains informational and never gates stock refill.

Construction stays silent, without adding a debug-mode field to the state.
Shared owners emit no INFO; accepted operational command mirrors belong
exclusively to ActivityLog.record_command.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from warehouse.configuration.warehouse_config import WarehouseConfig
from warehouse.domain.worker_type import WorkerType

if TYPE_CHECKING:
    from warehouse.workflow.inventory import Inventory
    from warehouse.workflow.loading_workflow import LoadingWorkflow
    from warehouse.workflow.order_registry import OrderRegistry
    from warehouse.workflow.picking_workflow import PickingWorkflow
    from warehouse.workflow.sequencing_workflow import SequencingWorkflow


class WarehouseState:
    """Compose shared owners without duplicating business queues or quantities."""

    orders: OrderRegistry
    picking: PickingWorkflow
    sequencing: SequencingWorkflow
    loading: LoadingWorkflow
    inventory: Inventory
    ready_replenishers: set[str]

    def __init__(self, config: WarehouseConfig) -> None:
        """Construct the five owners from one validated startup snapshot."""
        from warehouse.workflow.inventory import Inventory
        from warehouse.workflow.loading_workflow import LoadingWorkflow
        from warehouse.workflow.order_registry import OrderRegistry
        from warehouse.workflow.picking_workflow import PickingWorkflow
        from warehouse.workflow.sequencing_workflow import SequencingWorkflow

        self.orders = OrderRegistry(config)
        self.inventory = Inventory(config)
        self.picking = PickingWorkflow(
            frozenset(config.employees[WorkerType.PICKER]), config.location_map,
        )
        self.sequencing = SequencingWorkflow(
            frozenset(config.employees[WorkerType.SEQUENCER]),
        )
        self.loading = LoadingWorkflow(config)
        self.ready_replenishers = set()
