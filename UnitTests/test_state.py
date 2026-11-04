"""Verify enums, catalog immutability and diagnostics; reserve composed state.

Future tests will verify the five state owners, shared request identity,
collection isolation and informational replenisher readiness. Fixtures must
initialize numeric settings once before constructing mutable state.
"""

from __future__ import annotations

import ast
import io
import json
import logging
import re
import tempfile
import unittest
from collections import UserDict
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError
from enum import StrEnum
from pathlib import Path
from unittest.mock import patch

from UnitTests import assert_callable_traces, isolated_console_logging
from UnitTests.test_configuration import write_configuration
from warehouse.configuration.warehouse_config import WarehouseConfig
from warehouse.domain.location import Location
from warehouse.domain.order import Order
from warehouse.domain.product_catalog import ProductCatalog
from warehouse.domain.request_status import RequestStatus
from warehouse.domain.worker_type import WorkerType
from warehouse.file_io.activity_log import ActivityLog
from warehouse.launcher import _configure_console_logging, warehouse
from warehouse.routing.route_planner import RoutePlanner


class SharedEnumTests(unittest.TestCase):
    """Protect the exact vocabulary without implementing request transitions."""

    def test_worker_type_has_exact_str_enum_contract(self) -> None:
        self.assertEqual(WorkerType.__bases__, (StrEnum,))
        self.assertEqual(WorkerType.__module__, "warehouse.domain.worker_type")
        self.assertEqual(
            {name: member.value for name, member in WorkerType.__members__.items()},
            {
                "PICKER": "picker", "SEQUENCER": "sequencer",
                "LOADER": "loader", "REPLENISHER": "replenisher",
            },
        )
        self.assertEqual(len(WorkerType.__members__), len(WorkerType))

    def test_request_status_has_exact_str_enum_contract(self) -> None:
        self.assertEqual(RequestStatus.__bases__, (StrEnum,))
        self.assertEqual(RequestStatus.__module__, "warehouse.domain.request_status")
        self.assertEqual(
            {name: member.value for name, member in RequestStatus.__members__.items()},
            {
                "CREATED": "created",
                "WAITING_FOR_PICKING": "waiting_for_picking",
                "PICKING": "picking",
                "READY_FOR_SEQUENCING": "ready_for_sequencing",
                "SEQUENCING": "sequencing",
                "AWAITING_SEQUENCE_RESCAN": "awaiting_sequence_rescan",
                "REWORK_REQUIRED": "rework_required",
                "READY_FOR_LOADING": "ready_for_loading",
                "LOADING": "loading",
                "AWAITING_LOAD_RESCAN": "awaiting_load_rescan",
                "LOADED": "loaded",
                "DISPATCHED": "dispatched",
            },
        )
        self.assertEqual(len(RequestStatus.__members__), len(RequestStatus))

    def test_every_member_serializes_as_its_string_value(self) -> None:
        for enum in (WorkerType, RequestStatus):
            for member in enum:
                with self.subTest(enum=enum, member=member.name):
                    self.assertIsInstance(member, str)
                    self.assertEqual(str(member), member.value)
                    self.assertEqual(f"{member}", member.value)
                    self.assertEqual("%s" % member, member.value)
                    self.assertEqual(json.loads(json.dumps(member)), member.value)
                    self.assertIs(enum(member.value), member)

    def test_request_status_rejects_unsupported_values(self) -> None:
        unsupported = (
            "INCOMPLETE", "incomplete", "FAILED", "failed", "failure",
            "CANCELLED", "cancelled", "canceled", "awaiting_rescan", "", None, 1,
        )
        for value in (*unsupported, *(member.name for member in RequestStatus),
                      *(f" {member.value} " for member in RequestStatus)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                RequestStatus(value)

    def test_enum_modules_have_no_custom_methods_or_application_dependencies(self) -> None:
        domain = Path(__file__).resolve().parent.parent / "warehouse" / "domain"
        for name in ("worker_type", "request_status"):
            with self.subTest(module=name):
                tree = ast.parse((domain / f"{name}.py").read_text(encoding="utf-8"))
                self.assertFalse(any(
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    for node in ast.walk(tree)
                ))
                imports = [
                    node.module if isinstance(node, ast.ImportFrom) else alias.name
                    for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                    for alias in node.names
                ]
                self.assertEqual(imports, ["__future__", "enum"])


class ProductCatalogImmutabilityTests(unittest.TestCase):
    """Check each mutation route independently of future state owners."""

    def setUp(self) -> None:
        self.logger = self.enterContext(isolated_console_logging())

    def test_catalog_copies_an_arbitrary_mapping_before_freezing(self) -> None:
        source = UserDict({(" Model ", " Blue "): ("001", "002")})
        catalog = ProductCatalog(source)
        canonical = catalog.resolve_skus("model", "blue")
        source[(" Model ", " Blue ")] = ("3", "4")
        source[("New", "Red")] = ("5", "6")
        del source[(" Model ", " Blue ")]
        source.clear()
        self.assertEqual(dict(catalog.products), {("model", "blue"): ("001", "002")})
        self.assertIs(catalog.resolve_skus("model", "blue"), canonical)
        with self.assertRaises(ValueError):
            catalog.resolve_skus("New", "Red")

    def test_mapping_assignment_and_deletion_preserve_contents(self) -> None:
        catalog = ProductCatalog({("Model", "Blue"): ("1", "2")})
        mapping = catalog.products
        canonical = mapping[("model", "blue")]
        before = dict(mapping)
        with self.assertRaises(TypeError):
            mapping[("model", "blue")] = ("3", "4")
        with self.assertRaises(TypeError):
            del mapping[("model", "blue")]
        self.assertEqual(dict(mapping), before)
        self.assertIs(catalog.products, mapping)
        self.assertIs(catalog.resolve_skus("model", "blue"), canonical)

    def test_catalog_retains_frozen_slots_and_blocks_field_changes(self) -> None:
        catalog = ProductCatalog({("Model", "Blue"): ("1", "2")})
        mapping = catalog.products
        self.assertTrue(ProductCatalog.__dataclass_params__.frozen)
        self.assertEqual(ProductCatalog.__slots__, ("products",))
        self.assertFalse(hasattr(catalog, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            catalog.products = {}
        with self.assertRaises(FrozenInstanceError):
            del catalog.products
        self.assertIs(catalog.products, mapping)
        self.assertEqual(catalog.resolve_skus("model", "blue"), ("1", "2"))

    def test_catalog_has_no_external_effects_or_runtime_owner_dependencies(self) -> None:
        before = (self.logger.handlers[:], self.logger.level, self.logger.propagate)
        with (
            patch("builtins.open") as open_file,
            patch("io.open") as path_open,
            patch("os.mkdir") as mkdir,
            patch("os.chdir") as chdir,
            patch("pathlib.Path.cwd") as cwd,
            patch("warehouse.launcher._configure_console_logging") as configure,
        ):
            catalog = ProductCatalog({("Model", "Blue"): ("1", "2")})
            self.assertEqual(catalog.resolve_skus("model", "blue"), ("1", "2"))
            with self.assertRaises(ValueError):
                catalog.resolve_skus("missing", "blue")
        for boundary in (open_file, path_open, mkdir, chdir, cwd, configure):
            boundary.assert_not_called()
        self.assertEqual(
            (self.logger.handlers[:], self.logger.level, self.logger.propagate), before
        )
        path = Path(__file__).resolve().parent.parent / "warehouse/domain/product_catalog.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertFalse(any(name.startswith("warehouse") for name in imports))


class ConstructorDiagnosticTests(unittest.TestCase):
    """Exercise generated construction without implementing later owner behavior."""

    def test_generated_constructors_trace_values_only_when_debug_enabled(self) -> None:
        self.enterContext(isolated_console_logging())
        for threshold in (logging.INFO, logging.DEBUG):
            with (
                self.subTest(threshold=threshold),
                redirect_stdout(io.StringIO()) as stdout,
                redirect_stderr(io.StringIO()) as stderr,
            ):
                _configure_console_logging(threshold)
                location = Location("A", 0, 1, 2)
                order = Order("O0001", "Model", "Blue", "1", "2")
                locations = {"1": location, "2": Location("A", 0, 1, 3)}
                planner = RoutePlanner(locations)
                catalog = ProductCatalog({("Model", "Blue"): ("1", "2")})
                self.assertEqual(catalog.resolve_skus(" MODEL ", "blue"), ("1", "2"))
                config = WarehouseConfig(
                    Path("Translation.CSV"), Path("Traversal.CSV"), Path("Stock.CSV"),
                    catalog, locations, {location: 6},
                    {role: (name,) for role, name in zip(WorkerType, ("Alice", "Sue", "Bill", "Ruby"))},
                    30, 5, 4, 4, 5,
                    4, 3, 3, 1, 1, 1, "%Y%m%d%H%M",
                )
                self.assertIs(config.catalog, catalog)
                self.assertIsNot(config.location_map, locations)
                self.assertEqual(dict(config.location_map), locations)
                self.assertIs(config.location_map["1"], location)
                self.assertIs(planner.location_map, locations)
                self.assertEqual(order.id, "O0001")
                self.assertEqual(hash(location), hash(Location("A", 0, 1, 2)))
                for value, field in (
                    (location, "zone"), (order, "id"), (planner, "location_map"),
                    (config, "default_stock"),
                    (catalog, "products"),
                ):
                    with self.assertRaises(FrozenInstanceError):
                        setattr(value, field, "changed")
                self.assertEqual(stderr.getvalue(), "")
                if threshold == logging.INFO:
                    self.assertEqual(stdout.getvalue(), "")
                    continue
                assert_callable_traces(self, stdout.getvalue(), (
                    "warehouse.domain.location.Location.__post_init__",
                    "warehouse.domain.order.Order.__post_init__",
                    "warehouse.routing.route_planner.RoutePlanner.__post_init__",
                    "warehouse.configuration.warehouse_config.WarehouseConfig.__post_init__",
                    "warehouse.domain.product_catalog.ProductCatalog.__init__",
                    "warehouse.domain.product_catalog.ProductCatalog.resolve_skus",
                ))
                for evidence in (
                    "order_ids=('O0001',)", "sku_evidence=('1', '2')", "row_count=2",
                    "Translation.CSV", "Traversal.CSV", "Stock.CSV", "default_stock",
                ):
                    self.assertIn(evidence, stdout.getvalue())

    def test_implemented_callable_inventory_has_trace_or_transport_exemption(self) -> None:
        self.enterContext(isolated_console_logging())
        with redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()):
            _configure_console_logging(logging.DEBUG)
            location = Location("A", 0, 0, 0)
            Order("O0001", "Model", "Blue", "1", "2")
            RoutePlanner({"1": location})
            catalog = ProductCatalog({("Model", "Blue"): ("1", "2")})
            catalog.resolve_skus("model", "blue")
            WarehouseConfig(
                Path("Translation.CSV"), Path("Traversal.CSV"), Path("Stock.CSV"),
                catalog, {"1": location}, {}, {}, 30, 5, 4, 4, 5,
                4, 3, 3, 1, 1, 1, "%Y%m%d%H%M",
            )
            log = ActivityLog(Path("unused-run"), stdout)
            log.mark_incomplete("fixture", {})
            with tempfile.TemporaryDirectory() as temporary:
                config_path, _ = write_configuration(Path(temporary).resolve() / "Inputs")
                warehouse([
                    "--config", str(config_path), "--orders", "Orders.TXT", "--debug", "T"
                ])
        traced = set(re.findall(r"\[DEBUG\] function=(\S+) action=entry", stdout.getvalue()))
        assert_callable_traces(self, stdout.getvalue(), tuple(sorted(traced)))
        # Emit/format/filter/fallback functions cannot recursively trace transport.
        transport = {
            "warehouse.launcher._CheckedStreamHandler.emit",
            "warehouse.launcher._below_warning",
            "warehouse.launcher._warning_or_higher",
            "warehouse.launcher._report_logging_failure",
            "warehouse.file_io.activity_log._context_fields",
            "warehouse.file_io.activity_log._format_context",
            # Recursive value rendering is part of formatting transport; tracing
            # it through the handler would recursively render its own arguments.
            "warehouse.file_io.activity_log._diagnostic_text",
            "warehouse.file_io.activity_log.ActivityLog.feedback",
        }
        implemented: set[str] = set()
        root = Path(__file__).resolve().parent.parent
        for path in (root / "warehouse").rglob("*.py"):
            module = ".".join(path.relative_to(root).with_suffix("").parts)
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for owner in (tree, *(node for node in tree.body if isinstance(node, ast.ClassDef))):
                for node in owner.body:
                    if not isinstance(node, ast.FunctionDef):
                        continue
                    body = node.body
                    if ast.get_docstring(node) is not None:
                        body = body[1:]
                    if all(
                        isinstance(statement, ast.Pass)
                        or isinstance(statement, ast.Expr)
                        and isinstance(statement.value, ast.Constant)
                        and statement.value.value is Ellipsis
                        for statement in body
                    ):
                        continue
                    prefix = f"{owner.name}." if isinstance(owner, ast.ClassDef) else ""
                    implemented.add(f"{module}.{prefix}{node.name}")
        self.assertEqual(implemented, traced | transport)
        self.assertTrue(traced.isdisjoint(transport))

    def test_logging_ownership_and_debug_option_stay_at_the_launcher_boundary(self) -> None:
        root = Path(__file__).resolve().parent.parent
        for path in (root / "warehouse").rglob("*.py"):
            with self.subTest(path=path):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                        self.assertNotEqual(node.func.attr, "info")
                        if path.name != "launcher.py":
                            self.assertNotIn(node.func.attr, ("addHandler", "basicConfig"))
                    if isinstance(node, ast.ClassDef):
                        for field in node.body:
                            if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name):
                                self.assertNotIn("debug", field.target.id.casefold())
                    if isinstance(node, ast.TypeAlias):
                        self.assertNotIn("debug", ast.unparse(node.value).casefold())


# TODO: Add composition smoke tests when WarehouseState construction is implemented.
