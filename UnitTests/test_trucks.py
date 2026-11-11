"""Verify Truck semantics with real validated, once-published numeric owners.

Each case runs in a fresh process. Direct public transitions and traversal do
not prove the future LoadingWorkflow, Application or manifest integration.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from UnitTests.test_configuration import PROJECT_ROOT, write_configuration
from UnitTests.test_workflows import SUCCESS_PATH, request_snapshot
from warehouse.domain.fulfillment_request import FulfillmentRequest
from warehouse.domain.order import Order
from warehouse.domain.request_status import RequestStatus
from warehouse.domain.truck import Truck


def truck_request(
    sequence: int, order_count: int = 4,
    status: RequestStatus = RequestStatus.CREATED,
) -> FulfillmentRequest:
    """Create real canonical data and reach status only through public edges."""
    orders = tuple(
        Order(f'O{sequence:04d}-{index}', ' MoDeL ', 'bLuE', '001', '002')
        for index in range(order_count)
    )
    request = FulfillmentRequest(
        f'R202611110930-{sequence:03d}', sequence, orders,
        ('001',) * order_count + ('002',) * order_count,
    )
    path = SUCCESS_PATH
    if status is RequestStatus.REWORK_REQUIRED:
        path = (*SUCCESS_PATH[:6], RequestStatus.REWORK_REQUIRED)
    for target in path[1:path.index(status) + 1]:
        request.transition_to(target)
    return request


def truck_snapshot(truck: Truck, candidates: tuple[FulfillmentRequest, ...] = ()) -> tuple:
    """Observe every contracted value and canonical object/container identity."""
    return (
        id(truck), truck.id, id(truck.id), id(truck.requests),
        truck.capacity_requests, truck.is_full(),
        tuple(request_snapshot(request) for request in truck.requests),
        tuple(request_snapshot(request) for request in candidates),
    )


CHILD_SETUP = """
import inspect
import io
import logging
import unittest
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from pathlib import Path
from typing import get_type_hints
from unittest.mock import patch
from UnitTests.test_trucks import truck_request, truck_snapshot
from UnitTests.test_workflows import request_snapshot
from warehouse.configuration.config_loader import ConfigLoader
from warehouse.domain import truck as truck_module
from warehouse.domain.fulfillment_request import FulfillmentRequest
from warehouse.domain.order import Order
from warehouse.domain.request_status import RequestStatus
from warehouse.domain.truck import Truck
from warehouse.file_io.activity_log import buffered_diagnostics
from warehouse.launcher import _initialize_numeric_constants, _configure_console_logging, _parse_arguments
config = ConfigLoader().load_config(Path(__import__('sys').argv[1]))
_initialize_numeric_constants(config)
case = unittest.TestCase()
"""


def run_truck_process(body: str, capacity: int = 5) -> subprocess.CompletedProcess:
    """Use real input files and one publication, never module resets or reloads."""
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        config_path, document = write_configuration(root / 'Inputs')
        document['warehouse'].update(
            default_stock=30, replenishment_threshold=5, orders_per_request=4,
            pallet_capacity_fascia=4, truck_capacity_requests=capacity,
        )
        config_path.write_text(json.dumps(document), encoding='utf-8')
        return subprocess.run(
            [sys.executable, '-c', CHILD_SETUP + textwrap.dedent(body), str(config_path)],
            cwd=root, env={**os.environ, 'PYTHONPATH': str(PROJECT_ROOT)},
            capture_output=True, text=True, check=False,
        )


class TruckProcessTestCase(unittest.TestCase):
    def run_fresh(self, body: str, capacity: int = 5) -> None:
        result = run_truck_process(body, capacity)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout + result.stderr, '')


class TruckContentTests(TruckProcessTestCase):
    def test_exact_public_contract_and_supplied_identifier(self) -> None:
        self.run_fresh("""
            for method, parameters, hints in (
                (Truck.__init__, ('self', 'truck_id'), {'truck_id': str, 'return': type(None)}),
                (Truck.record_request, ('self', 'request'),
                 {'request': FulfillmentRequest, 'return': type(None)}),
                (Truck.is_full, ('self',), {'return': bool}),
            ):
                signature = inspect.signature(method)
                assert tuple(signature.parameters) == parameters
                assert all(p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD and
                           p.default is inspect.Parameter.empty for p in signature.parameters.values())
                assert get_type_hints(method) == hints
            prop = Truck.capacity_requests
            assert isinstance(prop, property) and prop.fset is prop.fdel is None
            assert tuple(inspect.signature(prop.fget).parameters) == ('self',)
            assert get_type_hints(prop.fget) == {'return': int}
            assert get_type_hints(Truck) == {'id': str, 'requests': list[FulfillmentRequest]}
            supplied = ' Supplied Truck ID '
            truck = Truck(supplied)
            assert truck.id is supplied
            assert type(truck.requests) is list and truck.requests == []
            assert set(vars(truck)) == {'id', 'requests'}
            assert Truck.__bases__ == (object,)
            assert not hasattr(Truck, '__dataclass_fields__')
            other = Truck('second')
            assert other.requests == [] and other.requests is not truck.requests
            assert truck.__init__(supplied) is None
        """)

    def test_empty_partial_and_exact_full_queries_use_default_and_alternate_capacity(self) -> None:
        for capacity in (1, 2, 5, 7):
            with self.subTest(capacity=capacity):
                self.run_fresh("""
                    truck = Truck('T001')
                    assert truck.capacity_requests == config.truck_capacity_requests == truck_module.CAPACITY_REQUESTS
                    assert truck.is_full() is False
                    requests = tuple(truck_request(number) for number in range(1, config.truck_capacity_requests + 1))
                    before_requests = tuple(request_snapshot(request) for request in requests)
                    for index, request in enumerate(requests, 1):
                        assert truck.record_request(request) is None
                        assert len(truck.requests) == index
                        assert truck.requests[-1] is request
                        assert truck.is_full() is (index == config.truck_capacity_requests)
                    before = truck_snapshot(truck, requests)
                    for repeat in range(3):
                        assert truck.capacity_requests == config.truck_capacity_requests
                        assert truck.is_full() is True
                    assert truck_snapshot(truck, requests) == before
                    assert tuple(request_snapshot(request) for request in requests) == before_requests
                    assert set(vars(truck)) == {'id', 'requests'}
                """, capacity)

    def test_capacity_assignment_and_deletion_preserve_all_contents(self) -> None:
        self.run_fresh("""
            truck = Truck('T001')
            request = truck_request(21)
            truck.record_request(request)
            before = truck_snapshot(truck, (request,))
            with case.assertRaises(AttributeError): truck.capacity_requests = 99
            assert truck_snapshot(truck, (request,)) == before
            with case.assertRaises(AttributeError): del truck.capacity_requests
            assert truck_snapshot(truck, (request,)) == before
            assert 'capacity_requests' not in vars(truck)
        """)

    def test_duplicate_while_space_remains_preserves_complete_snapshots(self) -> None:
        self.run_fresh("""
            truck = Truck('T001')
            request = truck_request(21, 2, RequestStatus.AWAITING_LOAD_RESCAN)
            truck.record_request(request)
            before = truck_snapshot(truck, (request,))
            with case.assertRaisesRegex(ValueError, 'duplicate_request'):
                truck.record_request(request)
            assert truck_snapshot(truck, (request,)) == before
            assert truck.requests[0] is request and truck.is_full() is False
        """)

    def test_duplicate_at_capacity_and_distinct_overflow_preserve_every_object(self) -> None:
        self.run_fresh("""
            truck = Truck('T001')
            accepted = (truck_request(21, 4), truck_request(22, 1))
            candidate = truck_request(23, 3)
            for request in accepted: truck.record_request(request)
            before = truck_snapshot(truck, (*accepted, candidate))
            with case.assertRaisesRegex(ValueError, 'duplicate_request'):
                truck.record_request(accepted[0])
            assert truck_snapshot(truck, (*accepted, candidate)) == before
            with case.assertRaisesRegex(ValueError, 'capacity_exceeded'):
                truck.record_request(candidate)
            assert truck_snapshot(truck, (*accepted, candidate)) == before
            assert truck.requests[0] is accepted[0] and truck.requests[1] is accepted[1]
        """, 2)

    def test_each_full_and_partial_request_consumes_one_slot_and_retains_orders(self) -> None:
        self.run_fresh("""
            truck = Truck('T001')
            requests = tuple(truck_request(number, count) for number, count in
                             ((21, 4), (22, 1), (23, 2), (24, 3)))
            snapshots = tuple(request_snapshot(request) for request in requests)
            orders = tuple(request.orders for request in requests)
            for index, request in enumerate(requests, 1):
                assert truck.record_request(request) is None
                assert len(truck.requests) == index
                assert truck.is_full() is (index == 4)
            for stored, original, original_orders in zip(truck.requests, requests, orders, strict=True):
                assert stored is original and stored.orders is original_orders
                for stored_order, original_order in zip(stored.orders, original_orders, strict=True):
                    assert stored_order is original_order
                    assert (stored_order.model, stored_order.colour, stored_order.front_sku, stored_order.back_sku) == (
                        ' MoDeL ', 'bLuE', '001', '002')
            assert tuple(request_snapshot(request) for request in requests) == snapshots
        """, 4)

    def test_admission_order_is_preserved_without_selecting_numeric_sequences(self) -> None:
        self.run_fresh("""
            truck = Truck('T001')
            requests = tuple(truck_request(number) for number in (10, 2, 9))
            before = tuple(request_snapshot(request) for request in requests)
            for request in requests: truck.record_request(request)
            assert tuple(request.sequence for request in truck.requests) == (10, 2, 9)
            assert all(stored is supplied for stored, supplied in zip(truck.requests, requests, strict=True))
            assert tuple(request_snapshot(request) for request in truck.requests) == before
        """)

    def test_admission_has_no_status_policy_and_never_transitions_requests(self) -> None:
        self.run_fresh("""
            for status in RequestStatus:
                truck = Truck('T001')
                request = truck_request(21, 1, status)
                before = request_snapshot(request)
                assert truck.record_request(request) is None
                assert request_snapshot(request) == before
                assert request.status is status and truck.requests[0] is request
        """)

    def test_canonical_objects_are_publicly_traversable_without_filesystem_or_setup_effects(self) -> None:
        self.run_fresh("""
            logger = logging.getLogger('warehouse')
            before_logging = (logger.handlers[:], logger.level, logger.propagate, logger.filters[:])
            requests = (truck_request(21, 4), truck_request(22, 1))
            with ExitStack() as stack:
                guards = [stack.enter_context(patch(target)) for target in (
                    'builtins.open', 'io.open', 'os.mkdir', 'os.chdir', 'pathlib.Path.cwd',
                    'warehouse.launcher._configure_console_logging',
                )]
                truck = Truck('T001')
                for request in requests: truck.record_request(request)
                assert truck.capacity_requests == 5 and truck.is_full() is False
                for stored, original in zip(truck.requests, requests, strict=True):
                    assert stored is original
                    assert all(a is b for a, b in zip(stored.orders, original.orders, strict=True))
                with case.assertRaises(ValueError): truck.record_request(requests[0])
                for guard in guards: guard.assert_not_called()
            assert before_logging == (logger.handlers[:], logger.level, logger.propagate, logger.filters[:])
        """)
