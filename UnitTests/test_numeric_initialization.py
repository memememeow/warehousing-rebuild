"""Exercise numeric publication in fresh processes, without module resets.

Workflow consumers remain pending their real owners. A public Truck instance
tests its capacity query only; its stub constructor proves no state integration.
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
        result = subprocess.run(
            [sys.executable, "-c", CHILD_SETUP + textwrap.dedent(body), *map(str, paths)],
            cwd=PROJECT_ROOT, capture_output=True, text=True, check=False,
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
                try:
                    Truck('Tprobe').capacity_requests
                except RuntimeError:
                    pass
                else:
                    raise AssertionError('early capacity query accepted')
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
            assert sys.get_int_max_str_digits() == limit
        """, reverse=True)

    def test_successful_initialization_and_capacity_read_are_silent_in_every_mode(self) -> None:
        for options in ([], ["--debug", "F"], ["--debug", "T"]):
            self.run_fresh(f"""
                options = {options!r}
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    assert warehouse(['--config', str(path_a), '--orders', 'Unvalidated.TXT', *options]) == 1
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

    def test_cli_stops_at_pending_order_preflight_in_all_modes_without_artifacts(self) -> None:
        self.run_fresh("""
            orders = path_a.parent / 'orders.txt'
            for contents in (None, '', 'Order Model Blue\\n', 'Order Model Blue\\nMalformed late line extra\\n'):
                if contents is not None:
                    orders.write_text(contents)
                before = {p: p.read_bytes() for p in path_a.parent.rglob('*') if p.is_file()}
                for options in ([], ['--debug', 'F'], ['--debug', 'T']):
                    with ExitStack() as stack:
                        out = stack.enter_context(redirect_stdout(io.StringIO()))
                        err = stack.enter_context(redirect_stderr(io.StringIO()))
                        guards = [stack.enter_context(patch(target)) for target in (
                            'warehouse.launcher._initialize_numeric_constants',
                            'warehouse.domain.warehouse_state.WarehouseState',
                            'warehouse.file_io.order_reader.OrderReader.read_orders',
                            'warehouse.file_io.output_writer.OutputWriter.create_output_directory',
                        )]
                        assert warehouse(['--config', str(path_a), '--orders', str(orders), *options]) == 1
                        for guard in guards:
                            guard.assert_not_called()
                    assert 'pending_order_preflight' in err.getvalue()
                    assert '[INFO]' not in out.getvalue() + err.getvalue()
                    assert_unpublished()
                    assert before == {p: p.read_bytes() for p in path_a.parent.rglob('*') if p.is_file()}
        """)

    def test_launcher_failure_boundary_preserves_real_partial_state_and_transport_failures(self) -> None:
        # Inject the future coordination call after a REAL loader, solely to test
        # failure handling. This is not proof of successful full order preflight.
        for failure in ("none", "write", "flush", "short", "closed", "unavailable"):
            with self.subTest(failure=failure):
                self.run_fresh(f"""
                    failure = {failure!r}
                    alternate = ConfigLoader().load_config(path_b)
                    modules['warehouse.domain.truck'].initialize_truck_constants(alternate)
                    real_load = ConfigLoader.load_config
                    def load_then_initialize(loader, path):
                        config = real_load(loader, path)
                        _initialize_numeric_constants(config)
                        raise AssertionError('initialization unexpectedly succeeded')
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
                        stack.enter_context(patch.object(ConfigLoader, 'load_config', load_then_initialize))
                        guards = [stack.enter_context(patch(target)) for target in (
                            'warehouse.domain.warehouse_state.WarehouseState',
                            'warehouse.file_io.output_writer.OutputWriter.create_output_directory',
                            'warehouse.workflow.warehouse_application.WarehouseApplication.run',
                        )]
                        assert warehouse(['--config', str(path_a), '--orders', 'unvalidated.txt']) == 1
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
