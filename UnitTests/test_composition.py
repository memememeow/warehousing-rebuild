"""Verify real constructor composition without claiming operational integration."""

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


COMPOSITION_SETUP = '''
import io
import logging
import sys
from collections import deque
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from warehouse.configuration.config_loader import ConfigLoader
from warehouse.domain.warehouse_state import WarehouseState
from warehouse.domain.worker_type import WorkerType
from warehouse.file_io.activity_log import ActivityLog
from warehouse.file_io.order_reader import OrderReader
from warehouse.file_io.output_writer import OutputWriter
from warehouse.launcher import _configure_console_logging, _initialize_numeric_constants
from warehouse.routing.route_planner import RoutePlanner
from warehouse.workflow.inventory import Inventory
from warehouse.workflow.loading_workflow import LoadingWorkflow
from warehouse.workflow.order_registry import OrderRegistry
from warehouse.workflow.picking_workflow import PickingWorkflow
from warehouse.workflow.sequencing_workflow import SequencingWorkflow
from warehouse.workflow.warehouse_application import WarehouseApplication
config = ConfigLoader().load_config(Path(sys.argv[1]))
orders = OrderReader().read_orders(Path(sys.argv[2]))
assert orders == ((1, 'Model', 'BLUE'), (3, 'Unknown', 'Purple'))
_initialize_numeric_constants(config)
'''


class StateCompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.config_path, self.document = write_configuration(self.root / 'Inputs')
        self.orders_path = self.config_path.parent / 'Orders.TXT'
        self.orders_path.write_text('Order Model BLUE\n\nOrder Unknown Purple', encoding='utf-8')

    def run_fresh(self, body: str, *, defaults: bool = False) -> None:
        if defaults:
            self.document['warehouse'].update(
                default_stock=30, replenishment_threshold=5, orders_per_request=4,
                pallet_capacity_fascia=4, truck_capacity_requests=5,
            )
            self.document['identifiers'].update(
                first_order_number=1, first_request_number=1, first_truck_number=1,
                order_minimum_digits=4, request_minimum_digits=3, truck_minimum_digits=3,
            )
            self.config_path.write_text(json.dumps(self.document), encoding='utf-8')
        launch = self.enterContext(tempfile.TemporaryDirectory(dir=self.root))
        before = {p: p.read_bytes() for p in self.config_path.parent.iterdir() if p.is_file()}
        result = subprocess.run(
            [sys.executable, '-B', '-c', COMPOSITION_SETUP + textwrap.dedent(body),
             str(self.config_path), str(self.orders_path)],
            cwd=launch, env={**os.environ, 'PYTHONPATH': str(PROJECT_ROOT)},
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout + result.stderr, '')
        self.assertEqual(list(Path(launch).iterdir()), [])
        self.assertEqual(before, {p: p.read_bytes() for p in self.config_path.parent.iterdir() if p.is_file()})

    def test_real_state_constructs_each_owner_once_with_explicit_retained_dependencies(self) -> None:
        for defaults in (True, False):
            # Restore the alternate inputs after the default child process.
            self.config_path, self.document = write_configuration(self.config_path.parent)
            with self.subTest(defaults=defaults):
                self.run_fresh('''
                    owners = (OrderRegistry, Inventory, PickingWorkflow, SequencingWorkflow, LoadingWorkflow)
                    calls = []
                    operation_codes = {method.__code__ for owner in owners
                                       for name, method in vars(owner).items()
                                       if callable(method) and hasattr(method, '__code__') and name != '__init__'}
                    def profile(frame, event, result):
                        if event != 'call':
                            return
                        assert frame.f_code not in operation_codes, 'constructor called an operational stub'
                        if frame.f_code.co_name == '__init__':
                            assert frame.f_globals.get('__name__') not in ('warehouse.domain.order', 'warehouse.domain.fulfillment_request', 'warehouse.domain.truck')
                            instance = frame.f_locals.get('self')
                            if type(instance) in owners:
                                calls.append((type(instance), dict(frame.f_locals)))
                    sys.setprofile(profile)
                    try:
                        state = WarehouseState(config)
                    finally:
                        sys.setprofile(None)
                    assert [owner for owner, arguments in calls] == list(owners), calls
                    for field, owner in zip(('orders', 'inventory', 'picking', 'sequencing', 'loading'), owners):
                        assert type(getattr(state, field)) is owner
                        arguments = next(arguments for actual, arguments in calls if actual is owner)
                        assert arguments['self'] is getattr(state, field)
                        if owner in (OrderRegistry, Inventory, LoadingWorkflow):
                            assert arguments['config'] is config
                    assert state.orders.config is state.loading.config is config
                    assert state.orders.config.catalog is config.catalog
                    for component, role in ((state.picking, WorkerType.PICKER), (state.sequencing, WorkerType.SEQUENCER)):
                        arguments = next(arguments for owner, arguments in calls if owner is type(component))
                        assert type(arguments['employees']) is frozenset
                        assert component.employees is arguments['employees']
                        assert component.employees == frozenset(config.employees[role])
                    assert state.picking.location_map is config.location_map
                    assert next(args for owner, args in calls if owner is PickingWorkflow)['location_map'] is config.location_map
                    try:
                        state.picking.location_map['001'] = config.location_map['002']
                    except TypeError:
                        pass
                    else:
                        raise AssertionError('shared location map is mutable')
                    assert type(state.ready_replenishers) is set and state.ready_replenishers == set()
                ''', defaults=defaults)

    def test_authoritative_constructor_collections_and_initial_values_are_owned_once(self) -> None:
        for defaults in (True, False):
            self.config_path, self.document = write_configuration(self.config_path.parent)
            with self.subTest(defaults=defaults):
                self.run_fresh('''
                    state = WarehouseState(config)
                    assert set(vars(state)) == {'orders', 'inventory', 'picking', 'sequencing', 'loading', 'ready_replenishers'}
                    expected_collections = (
                        (state.orders, 'accepted_orders', dict), (state.orders, 'pending_orders', deque),
                        (state.orders, 'requests', dict), (state.inventory, 'replenishment_queue', deque),
                        (state.picking, 'picking_queue', deque), (state.picking, 'picker_assignments', dict),
                        (state.sequencing, 'sequencing_queue', deque), (state.sequencing, 'sequencer_assignments', dict),
                        (state.loading, 'loading_queue', dict), (state.loading, 'loader_assignments', dict),
                        (state.loading, 'dispatched_trucks', list),
                    )
                    collections = []
                    for owner, field, expected_type in expected_collections:
                        value = getattr(owner, field)
                        assert type(value) is expected_type and not value, (type(owner), field)
                        collections.append(value)
                    assert len({id(value) for value in collections}) == len(collections)
                    assert state.orders.input_closed is False
                    assert state.orders.next_order_number == config.first_order_number
                    assert state.orders.next_request_number == config.first_request_number
                    assert state.loading.next_request_number == config.first_request_number
                    assert state.loading.next_truck_number == config.first_truck_number
                    assert not hasattr(state.loading, 'current_truck')  # User-approved Step 19 boundary.
                    assert state.loading.config.employees[WorkerType.LOADER] is config.employees[WorkerType.LOADER]
                    expected_fields = (
                        (state.orders, {'config', 'accepted_orders', 'pending_orders', 'requests', 'next_order_number', 'next_request_number', 'input_closed'}),
                        (state.inventory, {'quantities', 'replenishment_queue'}),
                        (state.picking, {'employees', 'location_map', 'picking_queue', 'picker_assignments'}),
                        (state.sequencing, {'employees', 'sequencing_queue', 'sequencer_assignments'}),
                        (state.loading, {'config', 'loading_queue', 'loader_assignments', 'next_request_number', 'next_truck_number', 'dispatched_trucks'}),
                    )
                    for owner, fields in expected_fields:
                        assert set(vars(owner)) == fields, (type(owner), vars(owner))
                    assert type(state.inventory.quantities) is dict
                    assert tuple(state.inventory.quantities) == tuple(config.location_map.values())
                    expected = {location: config.default_stock for location in config.location_map.values()}
                    expected.update(config.initial_stock)
                    assert state.inventory.quantities == expected
                    assert len(expected) == len(config.location_map) == 4
                    assert [state.inventory.quantities[location] for location in config.location_map.values()] == [config.default_stock, 0, 6, config.default_stock]
                    for retained, canonical in zip(state.inventory.quantities, config.location_map.values(), strict=True):
                        assert retained is canonical
                    # No constructor publishes an Order/request or consumes an order ID.
                    assert state.orders.accepted_orders == state.orders.requests == {}
                ''', defaults=defaults)

    def test_separate_states_share_immutable_inputs_and_have_independent_mutable_collections(self) -> None:
        self.run_fresh('''
            first, second = WarehouseState(config), WarehouseState(config)
            assert first.orders.config is second.orders.config is config
            assert first.picking.location_map is second.picking.location_map is config.location_map
            for component in ('orders', 'inventory', 'picking', 'sequencing', 'loading'):
                left, right = getattr(first, component), getattr(second, component)
                assert left is not right
                for field, value in vars(left).items():
                    if isinstance(value, (dict, list, deque)):
                        assert value is not getattr(right, field), (component, field)
            first.ready_replenishers.add(config.employees[WorkerType.REPLENISHER][0])
            assert second.ready_replenishers == set()
            before = dict(first.inventory.quantities)
            first.ready_replenishers.clear()
            assert first.inventory.quantities == second.inventory.quantities == before
            # Synchronous refill behavior itself is Step 16, not proved by this check.
        ''')

    def test_application_retains_only_the_five_contracted_references_and_constructors_are_silent(self) -> None:
        self.run_fresh('''
            from unittest.mock import patch
            for threshold in (logging.INFO, logging.DEBUG):
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    _configure_console_logging(threshold)
                    logger = logging.getLogger('warehouse')
                    before = (logger.handlers[:], logger.level, logger.filters[:], logger.propagate)
                    with patch('builtins.open') as opened, patch('io.open') as path_open, patch('os.mkdir') as mkdir, patch('os.chdir') as chdir, patch('pathlib.Path.cwd') as cwd:
                        state = WarehouseState(config)
                        planner = RoutePlanner(config.location_map)
                        files = OutputWriter()
                        destination = Path('unused-output')
                        log = ActivityLog(destination, out)
                        application = WarehouseApplication(state, planner, files, log, destination)
                        assert set(vars(application)) == {'state', 'planner', 'files', 'log', 'output_directory'}
                        for field, value in (('state', state), ('planner', planner), ('files', files), ('log', log), ('output_directory', destination)):
                            assert getattr(application, field) is value, field
                        assert planner.location_map is state.picking.location_map
                        for boundary in (opened, path_open, mkdir, chdir, cwd): boundary.assert_not_called()
                    assert before == (logger.handlers[:], logger.level, logger.filters[:], logger.propagate)
                assert out.getvalue() == err.getvalue() == ''
        ''')

    def test_public_constructor_signatures_and_application_class_count_are_preserved(self) -> None:
        self.run_fresh('''
            import ast
            import inspect
            from typing import get_type_hints
            from warehouse.configuration.warehouse_config import WarehouseConfig
            from warehouse.domain.location import LocationMap
            for owner, arguments in (
                (WarehouseState, {'config': WarehouseConfig}),
                (OrderRegistry, {'config': WarehouseConfig}),
                (Inventory, {'config': WarehouseConfig}),
                (LoadingWorkflow, {'config': WarehouseConfig}),
                (PickingWorkflow, {'employees': frozenset[str], 'location_map': LocationMap}),
                (SequencingWorkflow, {'employees': frozenset[str]}),
                (WarehouseApplication, {'state': WarehouseState, 'planner': RoutePlanner, 'files': OutputWriter, 'log': ActivityLog, 'output_directory': Path}),
            ):
                parameters = inspect.signature(owner.__init__).parameters
                assert tuple(parameters) == ('self', *arguments), owner
                assert all(p.default is inspect.Parameter.empty and p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for p in parameters.values())
                assert get_type_hints(owner.__init__) == {**arguments, 'return': type(None)}, owner
                assert owner.__bases__ == (object,)
            root = Path(sys.modules['warehouse'].__file__).parent
            classes = [(path, node.name) for path in root.rglob('*.py')
                       for node in ast.parse(path.read_text()).body if isinstance(node, ast.ClassDef)]
            assert len(classes) == 21, classes  # 18 application classes, two enums, one existing logging adapter.
            assert {name for path, name in classes if name.endswith('Status') or name == 'WorkerType'} == {'RequestStatus', 'WorkerType'}
            tree = ast.parse((root / 'domain/warehouse_state.py').read_text())
            state = next(node for node in tree.body if isinstance(node, ast.ClassDef))
            constructor = next(node for node in state.body if isinstance(node, ast.FunctionDef) and node.name == '__init__')
            local_imports = {node.module for node in constructor.body if isinstance(node, ast.ImportFrom)}
            assert local_imports == {'warehouse.workflow.' + name for name in ('inventory', 'order_registry', 'picking_workflow', 'sequencing_workflow', 'loading_workflow')}
            assert not any(isinstance(node, ast.ImportFrom) and node.module.startswith('warehouse.workflow') for node in tree.body)
        ''')
