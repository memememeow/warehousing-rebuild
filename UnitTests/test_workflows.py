"""Verify request lifecycle/data and shared imports; reserve workflow integration.

Future cases protect duplicate SKU occurrences, route validation, exact pick
progress, synchronous refill, rescans, same-request rework and commit safety.
Ordered loading and truck replacement must preserve the global request sequence.

Planned logging checks keep selected entry traces, stock changes and lifecycle
events at DEBUG, buffer nested diagnostics until related commits complete, and
leave INFO commands exclusively to ActivityLog.record_command. Debug verbosity
must not change workflow behavior or add state and command fields.
"""

from __future__ import annotations

import ast
import inspect
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import get_args, get_type_hints

from UnitTests import isolated_console_logging
from warehouse.domain.fulfillment_request import FulfillmentRequest
from warehouse.domain.order import Order
from warehouse.domain.request_status import RequestStatus
from warehouse.domain.worker_type import WorkerType
from warehouse.workflow import warehouse_application


class SharedEnumIntegrationTests(unittest.TestCase):
    """Check current imports and typed consumers without claiming stub behavior."""

    def test_request_and_command_contracts_reference_canonical_enums(self) -> None:
        self.assertIs(get_type_hints(FulfillmentRequest)["_status"], RequestStatus)
        self.assertIs(get_type_hints(FulfillmentRequest.status.fget)["return"], RequestStatus)
        self.assertIs(
            get_type_hints(FulfillmentRequest.transition_to)["next_status"], RequestStatus
        )
        self.assertIs(get_args(warehouse_application.ReadyCommand.__value__)[0], WorkerType)
        for name, roles in (
            ("PickingCommand", (WorkerType.PICKER,)),
            ("MarshallingCommand", (WorkerType.PICKER,)),
            ("SequencingCommand", (WorkerType.SEQUENCER,)),
            ("RescanCommand", (WorkerType.SEQUENCER, WorkerType.LOADER)),
            ("LoadingCommand", (WorkerType.LOADER,)),
            ("ReplenishmentCommand", (WorkerType.REPLENISHER,)),
        ):
            with self.subTest(command=name):
                alias = getattr(warehouse_application, name)
                actual = get_args(get_args(alias.__value__)[0])
                self.assertEqual(len(actual), len(roles))
                for value, role in zip(actual, roles, strict=True):
                    self.assertIs(value, role)

    def test_cold_imports_in_each_consumer_order_have_no_cycles_or_startup_effects(self) -> None:
        root = Path(__file__).resolve().parent.parent
        modules = (
            "warehouse.domain.worker_type", "warehouse.domain.request_status",
            "warehouse.domain.product_catalog",
            "warehouse.file_io.order_reader", "warehouse.launcher",
            "warehouse.configuration.config_loader", "warehouse.configuration.warehouse_config",
            "warehouse.domain.fulfillment_request", "warehouse.domain.warehouse_state",
            "warehouse.workflow.warehouse_application", "warehouse.workflow.picking_workflow",
            "warehouse.workflow.sequencing_workflow", "warehouse.workflow.loading_workflow",
            "warehouse.workflow.inventory", "warehouse.workflow.order_registry",
            "warehouse.domain.truck",
        )
        script = """
import importlib
import logging
import sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
logger = logging.getLogger('warehouse')
root_logger = logging.getLogger()
before = (logger.handlers[:], logger.level, logger.propagate, logger.disabled,
          root_logger.handlers[:], root_logger.level)
def profile(frame, event, value):
    if event == 'call' and frame.f_globals.get('__name__', '').startswith('warehouse'):
        assert frame.f_code.co_name != '__init__', 'import-time construction'
        assert not frame.f_code.co_name.startswith('initialize_'), 'import-time numeric publication'
with patch('pathlib.Path.cwd') as cwd, patch('os.chdir') as chdir, patch('os.mkdir') as mkdir, patch('builtins.open') as opened, patch('io.open') as path_open:
    sys.setprofile(profile)
    try:
        for name in sys.argv[2:]:
            importlib.import_module(name)
    finally:
        sys.setprofile(None)
    for boundary in (cwd, chdir, mkdir, opened, path_open): boundary.assert_not_called()
from warehouse.workflow import inventory, order_registry, sequencing_workflow, loading_workflow
from warehouse.domain import truck
owners = (inventory, order_registry, sequencing_workflow, loading_workflow, truck)
assert sum(len(module.__annotations__) for module in owners) == 11
assert all(name not in vars(module) for module in owners for name in module.__annotations__)
from warehouse.domain.worker_type import WorkerType
from warehouse.domain.request_status import RequestStatus
from warehouse.configuration import warehouse_config
from warehouse.domain import product_catalog
from warehouse.domain import fulfillment_request
from warehouse.workflow import warehouse_application
assert warehouse_config.WorkerType is WorkerType
assert warehouse_config.ProductCatalog is product_catalog.ProductCatalog
assert warehouse_application.WorkerType is WorkerType
assert fulfillment_request.RequestStatus is RequestStatus
after = (logger.handlers[:], logger.level, logger.propagate, logger.disabled,
         root_logger.handlers[:], root_logger.level)
assert before == after, (before, after)
"""
        with tempfile.TemporaryDirectory() as temporary:
            launch = Path(temporary)
            for first in modules:
                with self.subTest(first=first):
                    ordered = (first, *(name for name in modules if name != first))
                    result = subprocess.run(
                        [sys.executable, "-c", script, str(root), *ordered],
                        cwd=launch, capture_output=True, text=True, check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(result.stderr, "")
                    self.assertEqual(list(launch.iterdir()), [])


SUCCESS_PATH = (
    RequestStatus.CREATED, RequestStatus.WAITING_FOR_PICKING, RequestStatus.PICKING,
    RequestStatus.READY_FOR_SEQUENCING, RequestStatus.SEQUENCING,
    RequestStatus.AWAITING_SEQUENCE_RESCAN, RequestStatus.READY_FOR_LOADING,
    RequestStatus.LOADING, RequestStatus.AWAITING_LOAD_RESCAN,
    RequestStatus.LOADED, RequestStatus.DISPATCHED,
)
LEGAL_EDGES = frozenset(zip(SUCCESS_PATH, SUCCESS_PATH[1:])) | {
    (RequestStatus.AWAITING_SEQUENCE_RESCAN, RequestStatus.REWORK_REQUIRED),
    (RequestStatus.REWORK_REQUIRED, RequestStatus.WAITING_FOR_PICKING),
}


def request_at(status: RequestStatus) -> FulfillmentRequest:
    """Reach a state through legal public calls, never by installing private state."""
    orders = (
        Order('O0137', 'MoDeL', 'bLuE', '001', '090'),
        Order('O0141', 'MoDeL', 'bLuE', '001', '090'),
    )
    request = FulfillmentRequest('R202611110930-021', 21, orders, ('001', '001', '090', '090'))
    path = SUCCESS_PATH
    if status is RequestStatus.REWORK_REQUIRED:
        path = (*SUCCESS_PATH[:6], RequestStatus.REWORK_REQUIRED)
    for target in path[1:path.index(status) + 1]:
        request.transition_to(target)
    return request


def request_snapshot(request: FulfillmentRequest) -> tuple[object, ...]:
    """Keep values and identities separate so equality cannot hide replaced objects."""
    return (
        id(request), request.id, request.sequence, request.status,
        id(request.id), id(request.sequence),
        id(request.orders), tuple(id(order) for order in request.orders),
        tuple((order.id, order.model, order.colour, order.front_sku, order.back_sku)
              for order in request.orders),
        id(request.correct_sku_order), request.correct_sku_order, request.loading_skus(),
    )


class RequestLifecycleTests(unittest.TestCase):
    """Direct request behavior does not establish real owner handoffs or stock effects."""

    def setUp(self) -> None:
        self.enterContext(isolated_console_logging())

    def test_exact_public_signatures_and_type_hints(self) -> None:
        for method, parameters, hints in (
            (FulfillmentRequest.__init__, ('self', 'request_id', 'sequence', 'orders', 'correct_sku_order'),
             {'request_id': str, 'sequence': int, 'orders': tuple[Order, ...],
              'correct_sku_order': tuple[str, ...], 'return': type(None)}),
            (FulfillmentRequest.transition_to, ('self', 'next_status'),
             {'next_status': RequestStatus, 'return': type(None)}),
            (FulfillmentRequest.loading_skus, ('self',), {'return': tuple[str, ...]}),
        ):
            with self.subTest(method=method.__name__):
                signature = inspect.signature(method)
                self.assertEqual(tuple(signature.parameters), parameters)
                self.assertTrue(all(p.default is inspect.Parameter.empty and
                                    p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
                                    for p in signature.parameters.values()))
                self.assertEqual(get_type_hints(method), hints)
        for name, result in (('id', str), ('sequence', int), ('orders', tuple[Order, ...]),
                             ('correct_sku_order', tuple[str, ...]), ('status', RequestStatus)):
            with self.subTest(property=name):
                prop = getattr(FulfillmentRequest, name)
                self.assertIsInstance(prop, property)
                self.assertEqual(tuple(inspect.signature(prop.fget).parameters), ('self',))
                self.assertEqual(get_type_hints(prop.fget), {'return': result})
                self.assertIsNone(prop.fset)
                self.assertIsNone(prop.fdel)

    def test_slots_read_only_properties_and_tuple_evidence(self) -> None:
        request = request_at(RequestStatus.CREATED)
        self.assertEqual(FulfillmentRequest.__bases__, (object,))
        self.assertEqual(FulfillmentRequest.__slots__,
                         ('_id', '_sequence', '_orders', '_correct_sku_order', '_status'))
        self.assertFalse(hasattr(request, '__dict__'))
        self.assertFalse(hasattr(FulfillmentRequest, '__dataclass_fields__'))
        self.assertEqual({name for name in vars(FulfillmentRequest) if not name.startswith('_')},
                         {'id', 'sequence', 'orders', 'correct_sku_order', 'status',
                          'transition_to', 'loading_skus'})
        before = request_snapshot(request)
        for field in ('id', 'sequence', 'orders', 'correct_sku_order', 'status', 'debug'):
            with self.subTest(field=field):
                with self.assertRaises(AttributeError):
                    setattr(request, field, None)
                with self.assertRaises(AttributeError):
                    delattr(request, field)
                self.assertEqual(request_snapshot(request), before)
        self.assertIs(type(request.orders), tuple)
        self.assertIs(type(request.correct_sku_order), tuple)
        for evidence in (request.orders, request.correct_sku_order):
            with self.assertRaises(TypeError):
                evidence[0] = 'changed'
            with self.assertRaises(TypeError):
                del evidence[0]
        self.assertEqual(request_snapshot(request), before)

    def test_canonical_orders_and_original_spelling_survive_construction(self) -> None:
        order = Order('O0137', ' MoDeL ', 'bLuE', '001', '090')
        orders = (order, order)
        evidence = ('001', '001', '090', '090')
        request = FulfillmentRequest('supplied ID', 37, orders, evidence)
        self.assertEqual(request.id, 'supplied ID')
        self.assertEqual(request.sequence, 37)
        self.assertIs(request.status, RequestStatus.CREATED)
        self.assertEqual(request.correct_sku_order, evidence)
        self.assertIs(request.orders[0], order)
        self.assertIs(request.orders[1], order)
        self.assertEqual((order.id, order.model, order.colour, order.front_sku, order.back_sku),
                         ('O0137', ' MoDeL ', 'bLuE', '001', '090'))

    def test_every_allowed_and_disallowed_canonical_pair(self) -> None:
        self.assertEqual(len(LEGAL_EDGES), 12)
        for start in RequestStatus:
            for target in RequestStatus:
                with self.subTest(start=start, target=target):
                    request = request_at(start)
                    before = request_snapshot(request)
                    self.assertIs(request.status, start)
                    if (start, target) in LEGAL_EDGES:
                        self.assertIsNone(request.transition_to(target))
                        self.assertIs(request.status, target)
                        after = request_snapshot(request)
                        self.assertEqual(before[:3] + before[4:], after[:3] + after[4:])
                    else:
                        with self.assertRaisesRegex(ValueError, 'transition'):
                            request.transition_to(target)
                        self.assertEqual(request_snapshot(request), before)

    def test_string_statuses_and_other_noncanonical_targets_are_not_coerced(self) -> None:
        for target in (*[status.value for status in RequestStatus], 'PICKING',
                       ' waiting_for_picking ', None, 1, [], {}):
            with self.subTest(target=target):
                request = request_at(RequestStatus.CREATED)
                before = request_snapshot(request)
                with self.assertRaises(ValueError):
                    request.transition_to(target)
                self.assertEqual(request_snapshot(request), before)

    def test_complete_success_path_keeps_identity_and_terminal_state(self) -> None:
        request = request_at(RequestStatus.CREATED)
        before = request_snapshot(request)
        for target in SUCCESS_PATH[1:]:
            request.transition_to(target)
            after = request_snapshot(request)
            self.assertEqual(before[:3] + before[4:], after[:3] + after[4:])
        self.assertIs(request.status, RequestStatus.DISPATCHED)
        for target in RequestStatus:
            terminal = request_snapshot(request)
            with self.assertRaises(ValueError):
                request.transition_to(target)
            self.assertEqual(request_snapshot(request), terminal)

    def test_multiple_rework_attempts_use_the_same_request_and_orders(self) -> None:
        request = request_at(RequestStatus.CREATED)
        before = request_snapshot(request)
        request.transition_to(RequestStatus.WAITING_FOR_PICKING)
        for attempt in range(3):
            for target in (*SUCCESS_PATH[2:6], RequestStatus.REWORK_REQUIRED,
                           RequestStatus.WAITING_FOR_PICKING):
                request.transition_to(target)
                after = request_snapshot(request)
                self.assertEqual(before[:3] + before[4:], after[:3] + after[4:], attempt)
        for target in SUCCESS_PATH[2:]:
            request.transition_to(target)
        self.assertIs(request.status, RequestStatus.DISPATCHED)
        after = request_snapshot(request)
        self.assertEqual(before[:3] + before[4:], after[:3] + after[4:])

    def test_status_assignments_exist_only_in_constructor_and_transition(self) -> None:
        root = Path(__file__).resolve().parent.parent / 'warehouse'
        writers = []
        for path in root.rglob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for function in (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)):
                for node in ast.walk(function):
                    targets = node.targets if isinstance(node, ast.Assign) else (
                        (node.target,) if isinstance(node, (ast.AnnAssign, ast.AugAssign)) else ()
                    )
                    for target in targets:
                        if isinstance(target, ast.Attribute) and target.attr in ('_status', 'status'):
                            writers.append((path.relative_to(root).as_posix(), function.name, target.attr))
        self.assertCountEqual(writers, [('domain/fulfillment_request.py', '__init__', '_status'),
                                       ('domain/fulfillment_request.py', 'transition_to', '_status')])


# TODO (17–20): Exercise real owner commits, stock losses and canonical handoffs.
