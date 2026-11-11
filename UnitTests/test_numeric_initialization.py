"""Exercise numeric publication in fresh processes, without module resets.

Constructor counters and initial stock now consume published constants. Later
workflow operations remain pending their real owners. Truck's direct capacity,
contents and fullness checks do not prove future loading integration.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from UnitTests.test_configuration import PROJECT_ROOT, write_configuration


OWNER_FIELDS = {
    "warehouse.workflow.inventory": {
        "DEFAULT_STOCK": "default_stock",
        "REPLENISHMENT_THRESHOLD": "replenishment_threshold",
    },
    "warehouse.workflow.order_registry": {
        "ORDERS_PER_REQUEST": "orders_per_request",
        "FIRST_ORDER_NUMBER": "first_order_number",
        "ORDER_MINIMUM_DIGITS": "order_minimum_digits",
        "FIRST_REQUEST_NUMBER": "first_request_number",
        "REQUEST_MINIMUM_DIGITS": "request_minimum_digits",
    },
    "warehouse.workflow.sequencing_workflow": {
        "PALLET_CAPACITY_FASCIA": "pallet_capacity_fascia",
    },
    "warehouse.domain.truck": {"CAPACITY_REQUESTS": "truck_capacity_requests"},
    "warehouse.workflow.loading_workflow": {
        "FIRST_TRUCK_NUMBER": "first_truck_number",
        "TRUCK_MINIMUM_DIGITS": "truck_minimum_digits",
    },
}

INITIALIZERS = {
    "warehouse.workflow.inventory": "initialize_inventory_constants",
    "warehouse.workflow.order_registry": "initialize_order_constants",
    "warehouse.workflow.sequencing_workflow": "initialize_sequencing_constants",
    "warehouse.domain.truck": "initialize_truck_constants",
    "warehouse.workflow.loading_workflow": "initialize_loading_constants",
}

CHILD_SETUP = """
import importlib
import io
import json
import logging
import sys
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from pathlib import Path
from typing import Final, get_type_hints
from unittest.mock import patch
from UnitTests.test_numeric_initialization import OWNER_FIELDS, INITIALIZERS
from warehouse.configuration.config_loader import ConfigLoader
from warehouse.launcher import (
    _configure_console_logging, _initialize_numeric_constants,
    _numeric_initialization_state, warehouse,
)
modules = {name: importlib.import_module(name) for name in OWNER_FIELDS}
path_a, path_b = map(Path, sys.argv[1:3])

def published():
    return {name: {key: vars(module)[key] for key in OWNER_FIELDS[name]
                   if key in vars(module)} for name, module in modules.items()}

def assert_values(config):
    for name, module in modules.items():
        hints = get_type_hints(module)
        for constant, field in OWNER_FIELDS[name].items():
            assert hints[constant] == Final[int], (name, constant, hints)
            assert getattr(module, constant) == getattr(config, field), (name, field)
    assert len(_numeric_initialization_state()) == 11

def assert_unpublished():
    assert all(not values for values in published().values()), published()

"""


class NumericInitializationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.path_a, example = write_configuration(self.root / "Example")
        example["warehouse"].update(
            default_stock=30, replenishment_threshold=5, orders_per_request=4,
            pallet_capacity_fascia=4, truck_capacity_requests=5,
        )
        example["identifiers"].update(
            first_order_number=1, first_request_number=1, first_truck_number=1,
            order_minimum_digits=4, request_minimum_digits=3, truck_minimum_digits=3,
        )
        self.path_a.write_text(json.dumps(example), encoding="utf-8")
        self.path_b, self.alternate = write_configuration(self.root / "Alternate")

    def run_fresh(self, body: str, *, reverse: bool = False) -> subprocess.CompletedProcess:
        paths = (self.path_b, self.path_a) if reverse else (self.path_a, self.path_b)
        launch = self.enterContext(tempfile.TemporaryDirectory(dir=self.root))
        result = subprocess.run(
            [sys.executable, "-c", CHILD_SETUP + textwrap.dedent(body), *map(str, paths)],
            cwd=launch, env={**os.environ, 'PYTHONPATH': str(PROJECT_ROOT)},
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_cold_imports_and_all_early_reads_fail_without_publication(self) -> None:
        for names in (tuple(OWNER_FIELDS), tuple(reversed(OWNER_FIELDS))):
            code = textwrap.dedent("""
                import importlib, logging, sys
                from pathlib import Path
                before = (Path.cwd(), logging.getLogger().handlers[:],
                          logging.getLogger('warehouse').handlers[:])
                for name, constants in __import__('json').loads(sys.argv[1]).items():
                    module = importlib.import_module(name)
                    for constant in constants:
                        assert constant not in vars(module)
                        try:
                            getattr(module, constant)
                        except RuntimeError as error:
                            assert constant in str(error) and 'before' in str(error)
                        else:
                            raise AssertionError((name, constant))
                    try:
                        getattr(module, 'unknown_numeric_setting')
                    except AttributeError:
                        pass
                    else:
                        raise AssertionError('unknown attribute did not reject')
                from warehouse.domain.truck import Truck
                from warehouse.domain.fulfillment_request import FulfillmentRequest
                from warehouse.domain.order import Order
                truck = Truck('Tprobe')
                request = FulfillmentRequest('supplied', 21, (Order('O1', 'Model', 'Blue', '1', '2'),), ('1', '2'))
                for operation in (lambda: truck.capacity_requests, truck.is_full,
                                  lambda: truck.record_request(request)):
                    try:
                        operation()
                    except RuntimeError as error:
                        assert 'before numeric initialization' in str(error)
                    else:
                        raise AssertionError('early capacity consumer accepted')
                    assert truck.id == 'Tprobe' and truck.requests == []
                    assert request.status.value == 'created'
                assert before == (Path.cwd(), logging.getLogger().handlers[:],
                                  logging.getLogger('warehouse').handlers[:])
            """)
            before = sorted(self.root.rglob("*"))
            result = subprocess.run(
                [sys.executable, "-c", code, json.dumps({name: tuple(OWNER_FIELDS[name]) for name in names})],
                cwd=self.root, env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout + result.stderr, "")
            self.assertEqual(sorted(self.root.rglob("*")), before)

    def test_real_example_and_alternate_values_in_both_fresh_process_orders(self) -> None:
        for reverse in (False, True, True, False):
            with self.subTest(reverse=reverse):
                self.run_fresh("""
                    assert_unpublished()
                    config = ConfigLoader().load_config(path_a)
                    assert_unpublished()
                    _initialize_numeric_constants(config)
                    assert_values(config)
                """, reverse=reverse)

    def test_each_owner_rejects_identical_and_different_reinitialization_unchanged(self) -> None:
        for reverse in (False, True):
            self.run_fresh("""
                config = ConfigLoader().load_config(path_a)
                alternate = ConfigLoader().load_config(path_b)
                # Exercise owner helpers directly as well as the coordinator.
                for name, module in modules.items():
                    getattr(module, INITIALIZERS[name])(config)
                assert_values(config)
                before = published()
                for repeated in (config, alternate):
                    for name, module in modules.items():
                        try:
                            getattr(module, INITIALIZERS[name])(repeated)
                        except RuntimeError as error:
                            assert 'already initialized' in str(error)
                        else:
                            raise AssertionError(name)
                        assert published() == before
                    try:
                        _initialize_numeric_constants(repeated)
                    except RuntimeError:
                        pass
                    else:
                        raise AssertionError('coordinator accepted repeat')
                    assert published() == before
            """, reverse=reverse)

    def test_constructor_consumers_reject_early_reads_and_use_published_owner_starts(self) -> None:
        for reverse in (False, True):
            self.run_fresh("""
                from warehouse.domain.warehouse_state import WarehouseState
                from warehouse.workflow.inventory import Inventory
                from warehouse.workflow.order_registry import OrderRegistry
                from warehouse.workflow.loading_workflow import LoadingWorkflow
                config = ConfigLoader().load_config(path_a)
                for owner in (Inventory, OrderRegistry, LoadingWorkflow, WarehouseState):
                    try:
                        owner(config)
                    except RuntimeError as error:
                        assert 'before numeric initialization' in str(error), str(error)
                    else:
                        raise AssertionError(owner)
                    assert_unpublished()
                # A published local truck start cannot hide the unpublished
                # cross-module request start. No constant is imported by value.
                modules['warehouse.workflow.loading_workflow'].initialize_loading_constants(config)
                try:
                    LoadingWorkflow(config)
                except RuntimeError as error:
                    assert 'order_registry.FIRST_REQUEST_NUMBER' in str(error)
                else:
                    raise AssertionError('unpublished registry start accepted')
                for name, module in modules.items():
                    if name != 'warehouse.workflow.loading_workflow':
                        getattr(module, INITIALIZERS[name])(config)
                state = WarehouseState(config)
                assert_values(config)
                assert state.orders.next_order_number == modules['warehouse.workflow.order_registry'].FIRST_ORDER_NUMBER
                assert state.orders.next_request_number == state.loading.next_request_number == modules['warehouse.workflow.order_registry'].FIRST_REQUEST_NUMBER
                assert state.loading.next_truck_number == modules['warehouse.workflow.loading_workflow'].FIRST_TRUCK_NUMBER
                assert not hasattr(state.loading, 'current_truck')
            """, reverse=reverse)

    def test_truck_capacity_uses_constant_through_public_read_only_property(self) -> None:
        self.run_fresh("""
            config = ConfigLoader().load_config(path_a)
            _initialize_numeric_constants(config)
            from warehouse.domain.truck import Truck
            truck = Truck('Tprobe')
            assert truck.capacity_requests == config.truck_capacity_requests
            assert Truck.capacity_requests.fset is None
            try:
                truck.capacity_requests = 999
            except AttributeError:
                pass
            else:
                raise AssertionError('capacity setter accepted')
            assert truck.capacity_requests == config.truck_capacity_requests
            assert 'capacity_requests' not in vars(truck)
        """, reverse=True)

    def test_constants_have_only_the_prescribed_annotation_owners_and_no_defaults(self) -> None:
        expected = {constant: name for name, values in OWNER_FIELDS.items() for constant in values}
        declarations = {}
        for path in (PROJECT_ROOT / "warehouse").rglob("*.py"):
            module = ".".join(path.relative_to(PROJECT_ROOT).with_suffix("").parts)
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    if node.target.id in expected:
                        self.assertIsNone(node.value, (module, node.target.id))
                        declarations.setdefault(node.target.id, []).append(module)
                if isinstance(node, ast.ImportFrom):
                    self.assertTrue(all(alias.name not in expected for alias in node.names), module)
        self.assertEqual(declarations, {name: [owner] for name, owner in expected.items()})

    def test_truck_capacity_rejects_zero_and_invalid_types_before_publication(self) -> None:
        self.run_fresh("""
            original = json.loads(path_a.read_text())
            for invalid in (0, -1, True, False, 1.5, '5'):
                document = json.loads(json.dumps(original))
                document['warehouse']['truck_capacity_requests'] = invalid
                path_a.write_text(json.dumps(document))
                try:
                    ConfigLoader().load_config(path_a)
                except ValueError as error:
                    assert 'truck_capacity_requests' in str(error)
                else:
                    raise AssertionError('invalid truck capacity accepted')
                assert_unpublished()
        """)

    def test_missing_and_invalid_required_settings_reject_before_publication(self) -> None:
        self.run_fresh("""
            original = json.loads(path_a.read_text())
            fields = [field for values in OWNER_FIELDS.values() for field in values.values()]
            for field in fields:
                section = 'warehouse' if field in original['warehouse'] else 'identifiers'
                for invalid in ('missing', True, 1.5, -1, '4'):
                    document = json.loads(json.dumps(original))
                    if invalid == 'missing':
                        del document[section][field]
                    else:
                        document[section][field] = invalid
                    path_a.write_text(json.dumps(document))
                    try:
                        ConfigLoader().load_config(path_a)
                    except ValueError as error:
                        assert field in str(error)
                    else:
                        raise AssertionError((field, invalid))
                    assert_unpublished()
        """)

    def test_large_configured_values_publish_exactly_without_changing_conversion_limit(self) -> None:
        document = self.alternate
        for section in ("warehouse", "identifiers"):
            for field, value in document[section].items():
                if type(value) is int:
                    document[section][field] = "LARGE"
        document["warehouse"]["replenishment_threshold"] = 0
        self.path_b.write_text(json.dumps(document).replace('"LARGE"', "9" * 5000), encoding="utf-8")
        self.run_fresh("""
            limit = sys.get_int_max_str_digits()
            config = ConfigLoader().load_config(path_a)
            _initialize_numeric_constants(config)
            assert_values(config)
            assert config.first_request_number == 10 ** 5000 - 1
            assert modules['warehouse.domain.truck'].Truck('Tprobe').capacity_requests == 10 ** 5000 - 1
            from warehouse.domain.fulfillment_request import FulfillmentRequest
            from warehouse.domain.order import Order
            truck = modules['warehouse.domain.truck'].Truck('Tlarge')
            request = FulfillmentRequest('supplied', 21, (Order('O1', 'Model', 'Blue', '1', '2'),), ('1', '2'))
            with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                _configure_console_logging(logging.DEBUG)
                truck.record_request(request)
            assert len(out.getvalue().splitlines()) == 1 and err.getvalue() == ''
            assert 'capacity_requests=' + '9' * 5000 in out.getvalue()
            assert truck.requests[0] is request and truck.is_full() is False
            assert sys.get_int_max_str_digits() == limit
        """, reverse=True)

    def test_successful_initialization_and_capacity_read_are_silent_in_every_mode(self) -> None:
        for options in ([], ["--debug", "F"], ["--debug", "T"]):
            self.run_fresh(f"""
                options = {options!r}
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    _configure_console_logging(logging.DEBUG if 'T' in options else logging.INFO)
                    config = ConfigLoader().load_config(path_a)
                    out.seek(0); out.truncate(); err.seek(0); err.truncate()
                    _initialize_numeric_constants(config)
                    assert modules['warehouse.domain.truck'].Truck('Tprobe').capacity_requests == config.truck_capacity_requests
                assert out.getvalue() == err.getvalue() == ''
            """)

    def test_coordinator_failure_reports_partial_publication_without_rollback(self) -> None:
        self.run_fresh("""
            config = ConfigLoader().load_config(path_a)
            alternate = ConfigLoader().load_config(path_b)
            modules['warehouse.domain.truck'].initialize_truck_constants(alternate)
            try:
                _initialize_numeric_constants(config)
            except RuntimeError as error:
                assert 'truck' in str(error)
            else:
                raise AssertionError('repeat capacity accepted')
            after = published()
            assert after['warehouse.domain.truck']['CAPACITY_REQUESTS'] == alternate.truck_capacity_requests
            assert after['warehouse.workflow.inventory']['DEFAULT_STOCK'] == config.default_stock
            assert after['warehouse.workflow.order_registry']['FIRST_REQUEST_NUMBER'] == config.first_request_number
            assert after['warehouse.workflow.sequencing_workflow']['PALLET_CAPACITY_FASCIA'] == config.pallet_capacity_fascia
            assert after['warehouse.workflow.loading_workflow'] == {}
            state = _numeric_initialization_state()
            assert state['warehouse.workflow.loading_workflow.FIRST_TRUCK_NUMBER'] == 'unpublished'
            assert state['warehouse.domain.truck.CAPACITY_REQUESTS'] == alternate.truck_capacity_requests
            try:
                _initialize_numeric_constants(alternate)
            except RuntimeError:
                pass
            else:
                raise AssertionError('repeat coordination accepted')
            assert published() == after
        """)

    def test_cli_help_argument_errors_and_invalid_configuration_never_initialize(self) -> None:
        self.run_fresh("""
            with ExitStack() as stack:
                stack.enter_context(redirect_stdout(io.StringIO()))
                stack.enter_context(redirect_stderr(io.StringIO()))
                guards = [stack.enter_context(patch(target)) for target in (
                    'warehouse.launcher._initialize_numeric_constants',
                    'warehouse.domain.warehouse_state.WarehouseState',
                    'warehouse.file_io.output_writer.OutputWriter.create_output_directory',
                    'warehouse.workflow.warehouse_application.WarehouseApplication.run',
                )]
                for argv in (['--help'], [], ['--config', str(path_a)],
                             ['--config', str(path_a), '--orders', 'x', '--debug'],
                             ['--config', str(path_a), '--orders', 'x', '--debug', 'invalid']):
                    try:
                        warehouse(argv)
                    except SystemExit as error:
                        assert error.code in (0, 2)
                    else:
                        raise AssertionError(argv)
                    assert_unpublished()
                for contents in ('{}', '{', '{"files":{}}'):
                    path_a.write_text(contents)
                    assert warehouse(['--config', str(path_a), '--orders', 'x']) == 1
                    assert_unpublished()
                assert warehouse(['--config', str(path_a.parent / 'missing.json'), '--orders', 'x']) == 1
                assert_unpublished()
                for guard in guards:
                    guard.assert_not_called()
        """)

    def test_real_startup_preflights_all_lines_then_initializes_once_in_every_mode(self) -> None:
        for reverse in (False, True):
            for contents, expected in (
                ("", ()), (" \n\t\n", ()),
                ("\nOrder Model BLUE\n\t\norder Model BLUE\nOrder Unknown Purple",
                 ((2, "Model", "BLUE"), (4, "Model", "BLUE"), (5, "Unknown", "Purple"))),
            ):
                for options in ([], ["--debug", "F"], ["--debug", "T"]):
                    with self.subTest(reverse=reverse, contents=contents, options=options):
                        self.run_fresh(f"""
                            from warehouse.file_io.order_reader import OrderReader
                            from warehouse.file_io.output_writer import OutputWriter
                            orders_path = path_a.parent / 'orders.txt'
                            orders_path.write_text({contents!r}, encoding='utf-8')
                            before = {{p: p.read_bytes() if p.is_file() else None
                                      for p in path_a.parent.rglob('*')}}
                            events, returned, lines, states = [], [], [], []
                            def profile(frame, event, value):
                                if frame.f_globals.get('__name__') == 'warehouse.domain.warehouse_state' and frame.f_code.co_name == '__init__':
                                    if event == 'call':
                                        assert events == ['config', 'orders', 'numeric'], events
                                        assert frame.f_locals['config'] is returned[0]
                                        assert_values(returned[0])
                                        events.append('state')
                                    elif event == 'return':
                                        states.append(frame.f_locals['self'])
                                        assert states[-1].orders.config is states[-1].loading.config is returned[0]
                            real_load = ConfigLoader.load_config
                            real_read = OrderReader.read_orders
                            real_parse = OrderReader.parse_order
                            real_initialize = _initialize_numeric_constants
                            real_create = OutputWriter.create_output_directory
                            def load(loader, path):
                                assert_unpublished()
                                config = real_load(loader, path)
                                events.append('config')
                                returned.append(config)
                                return config
                            def parse(reader, text, source, line_number):
                                assert_unpublished()
                                lines.append(line_number)
                                return real_parse(reader, text, source, line_number)
                            def read(reader, path):
                                assert events == ['config']
                                result = real_read(reader, path)
                                assert type(result) is tuple and result == {expected!r}, result
                                events.append('orders')
                                returned.append(result)
                                return result
                            def initialize(config):
                                assert events == ['config', 'orders']
                                assert sys._getframe(1).f_locals['orders'] is returned[1]
                                assert_unpublished()
                                real_initialize(config)
                                assert_values(config)
                                events.append('numeric')
                            def create(writer, working_directory, started_at):
                                assert events == ['config', 'orders', 'numeric', 'state']
                                assert_values(returned[0])
                                assert started_at.utcoffset() is not None
                                selected = real_create(writer, working_directory, started_at)
                                assert selected.is_absolute() and selected.is_dir()
                                assert selected.parent == Path.cwd()
                                assert not list(selected.iterdir())
                                returned.append(selected)
                                events.append('folder')
                                return selected
                            out, err = io.StringIO(), io.StringIO()
                            with ExitStack() as stack:
                                stack.enter_context(redirect_stdout(out))
                                stack.enter_context(redirect_stderr(err))
                                stack.enter_context(patch.object(ConfigLoader, 'load_config', load))
                                stack.enter_context(patch.object(OrderReader, 'read_orders', read))
                                stack.enter_context(patch.object(OrderReader, 'parse_order', parse))
                                stack.enter_context(patch('warehouse.launcher._initialize_numeric_constants', initialize))
                                stack.enter_context(patch.object(OutputWriter, 'create_output_directory', create))
                                guards = [stack.enter_context(patch(target)) for target in (
                                    'warehouse.workflow.warehouse_application.WarehouseApplication.run',
                                    'os.chdir',
                                )]
                                sys.setprofile(profile)
                                try:
                                    assert warehouse(['--config', str(path_a), '--orders', str(orders_path), *{options!r}]) == 1
                                finally:
                                    sys.setprofile(None)
                                for guard in guards: guard.assert_not_called()
                            assert events == ['config', 'orders', 'numeric', 'state', 'folder'], events
                            assert len(states) == 1
                            assert lines == list(range(1, {len(contents.splitlines())} + 1)), lines
                            assert_values(returned[0])
                            assert 'reason=pending_simulation' in err.getvalue()
                            assert 'pending_order_preflight' not in err.getvalue()
                            assert 'pending_numeric_integration' not in err.getvalue()
                            assert len(err.getvalue().splitlines()) == 1
                            assert '[INFO]' not in out.getvalue() + err.getvalue()
                            assert 'initialized' not in out.getvalue()
                            assert bool(out.getvalue()) == ('T' in {options!r})
                            assert before == {{p: p.read_bytes() if p.is_file() else None
                                              for p in path_a.parent.rglob('*')}}
                        """, reverse=reverse)

    def test_real_order_failures_never_publish_constants_or_construct_outputs(self) -> None:
        for case in ('missing', 'directory', 'grammar', 'utf8', 'late_utf8', 'open', 'read'):
            for options in ([], ["--debug", "F"], ["--debug", "T"]):
                with self.subTest(case=case, options=options):
                    self.run_fresh(f"""
                        orders = path_a.parent / {f'{case}-{options[-1] if options else "omitted"}.txt'!r}
                        case = {case!r}
                        contents = b'Order Model Blue\\nOrder missing'
                        if case == 'directory': orders.mkdir()
                        elif case != 'missing':
                            if case == 'utf8': contents = b'\\xff'
                            elif case == 'late_utf8': contents = b'Order Model Blue\\n' * 2000 + b'\\xff'
                            elif case in ('open', 'read'): contents = b'Order Model Blue'
                            orders.write_bytes(contents)
                        before = {{p: p.read_bytes() if p.is_file() else None
                                  for p in path_a.parent.rglob('*')}}
                        real_open = io.open
                        def fail_read(*args):
                            raise OSError('order device failed')
                        def open_file(path, *args, **kwargs):
                            if Path(path) == orders:
                                if case == 'open': raise PermissionError('order access denied')
                                stream = io.BytesIO(contents)
                                stream.read = fail_read
                                return stream
                            return real_open(path, *args, **kwargs)
                        out, err = io.StringIO(), io.StringIO()
                        with ExitStack() as stack:
                            stack.enter_context(redirect_stdout(out))
                            stack.enter_context(redirect_stderr(err))
                            if case in ('open', 'read'):
                                stack.enter_context(patch('io.open', open_file))
                            guards = [stack.enter_context(patch(target)) for target in (
                                'warehouse.launcher._initialize_numeric_constants',
                                'warehouse.domain.warehouse_state.WarehouseState',
                                'warehouse.file_io.output_writer.OutputWriter.create_output_directory',
                                'warehouse.workflow.warehouse_application.WarehouseApplication.run',
                                'os.chdir', 'os.mkdir',
                            )]
                            assert warehouse(['--config', str(path_a), '--orders', str(orders), *{options!r}]) == 1
                            for guard in guards: guard.assert_not_called()
                        assert_unpublished()
                        assert 'reason=order_preflight_failed' in err.getvalue(), err.getvalue()
                        assert str(orders) in err.getvalue()
                        if case == 'grammar': assert 'line=2' in err.getvalue()
                        if case == 'late_utf8': assert f'byte_offset={{len(contents)-1}}' in err.getvalue()
                        assert len(err.getvalue().splitlines()) == 1, err.getvalue()
                        assert '[INFO]' not in out.getvalue() + err.getvalue()
                        assert 'action=validated path=' not in out.getvalue()
                        assert before == {{p: p.read_bytes() if p.is_file() else None
                                          for p in path_a.parent.rglob('*')}}
                    """)

    def test_launcher_failure_boundary_preserves_real_partial_state_and_transport_failures(self) -> None:
        # Preinitialize one owner to force a real post-preflight invariant failure.
        for failure in ("none", "write", "flush", "short", "closed", "unavailable"):
            with self.subTest(failure=failure):
                self.run_fresh(f"""
                    failure = {failure!r}
                    alternate = ConfigLoader().load_config(path_b)
                    modules['warehouse.domain.truck'].initialize_truck_constants(alternate)
                    orders = path_a.parent / 'orders.txt'
                    orders.write_text('Order Model Blue', encoding='utf-8')
                    class ErrorStream(io.StringIO):
                        def write(self, text):
                            if failure == 'unavailable' or ('reason=startup_invariant_failed' in text):
                                if failure in ('write', 'unavailable'):
                                    raise OSError('diagnostic write failed')
                                if failure == 'short':
                                    return 0
                                if failure == 'closed':
                                    closed = io.StringIO(); closed.close(); closed.write(text)
                            return super().write(text)
                        def flush(self):
                            if failure == 'flush' and 'reason=startup_invariant_failed' in self.getvalue():
                                raise ValueError('diagnostic flush failed')
                            return super().flush()
                    out, err = io.StringIO(), ErrorStream()
                    with ExitStack() as stack:
                        stack.enter_context(redirect_stdout(out))
                        stack.enter_context(redirect_stderr(err))
                        stack.enter_context(patch.object(logging, 'raiseExceptions', False))
                        guards = [stack.enter_context(patch(target)) for target in (
                            'warehouse.domain.warehouse_state.WarehouseState',
                            'warehouse.file_io.output_writer.OutputWriter.create_output_directory',
                            'warehouse.workflow.warehouse_application.WarehouseApplication.run',
                        )]
                        assert warehouse(['--config', str(path_a), '--orders', str(orders)]) == 1
                        for guard in guards:
                            guard.assert_not_called()
                    state = _numeric_initialization_state()
                    assert state['warehouse.workflow.inventory.DEFAULT_STOCK'] == 30
                    assert state['warehouse.domain.truck.CAPACITY_REQUESTS'] == alternate.truck_capacity_requests
                    assert state['warehouse.workflow.loading_workflow.FIRST_TRUCK_NUMBER'] == 'unpublished'
                    if failure != 'unavailable':
                        text = err.getvalue()
                        assert 'numeric_initialization=' in text
                        assert 'unpublished' in text and 'CAPACITY_REQUESTS' in text
                        assert 'last_confirmed_trace_line=n/a' in text
                        if failure != 'none':
                            assert 'console_logging_failed' in text
                    assert '[INFO]' not in out.getvalue() + err.getvalue()
                """)
