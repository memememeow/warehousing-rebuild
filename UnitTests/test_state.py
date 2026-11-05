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
from collections import Counter, UserDict
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
from warehouse.file_io.order_reader import OrderReader
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

    def test_constructors_preserve_identity_and_immutability_without_routine_logs(self) -> None:
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
                self.assertEqual(stdout.getvalue(), "")


    def test_retained_debug_event_inventory_matches_sources_context_and_individual_emissions(self) -> None:
        # Count source sites, including the two distinct SKU rejection branches.
        expected = Counter({
            ("warehouse.launcher.warehouse", "launcher.warehouse", "entry"): 1,
            ("warehouse.file_io.order_reader.OrderReader.read_orders", "order_reader.read_orders", "validated"): 1,
            ("warehouse.file_io.order_reader.OrderReader.read_orders", "order_reader.read_orders", "rejected"): 2,
            ("warehouse.file_io.order_reader.OrderReader.parse_order", "order_reader.parse_order", "rejected"): 1,
            ("warehouse.configuration.config_loader.ConfigLoader.load_config", "config_loader.load_config", "entry"): 1,
            ("warehouse.configuration.config_loader._read_json", "config_loader._read_json", "entry"): 1,
            ("warehouse.configuration.config_loader._read_csv", "config_loader._read_csv", "entry"): 1,
            ("warehouse.domain.product_catalog.ProductCatalog.__init__", "product_catalog.__init__", "rejected"): 1,
            ("warehouse.domain.product_catalog.ProductCatalog.resolve_skus", "product_catalog.resolve_skus", "rejected"): 1,
            ("warehouse.domain.product_catalog._normalize_product", "product_catalog._normalize_product", "rejected"): 1,
            ("warehouse.domain.product_catalog._validate_skus", "product_catalog._validate_skus", "rejected"): 2,
        })
        root = Path(__file__).resolve().parent.parent
        observed = Counter()
        for path in (root / "warehouse").rglob("*.py"):
            module = ".".join(path.relative_to(root).with_suffix("").parts)
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for owner in (tree, *(node for node in tree.body if isinstance(node, ast.ClassDef))):
                for function in owner.body:
                    if not isinstance(function, ast.FunctionDef):
                        continue
                    prefix = f"{owner.name}." if isinstance(owner, ast.ClassDef) else ""
                    identity = f"{module}.{prefix}{function.name}"
                    for call in ast.walk(function):
                        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                            continue
                        if not isinstance(call.func.value, ast.Name) or call.func.value.id != "logger" or call.func.attr != "debug":
                            continue
                        self.assertIsInstance(call.args[0], ast.Constant, identity)
                        match = re.match(r"function=(\S+) action=(\S+)", call.args[0].value)
                        self.assertIsNotNone(match, identity)
                        observed[(identity, *match.groups())] += 1
        self.assertEqual(observed, expected)
        labels = {identity: label for identity, label, action in expected}
        self.assertEqual(len(labels), len(set(labels.values())))
        self.enterContext(isolated_console_logging())
        with redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()):
            with tempfile.TemporaryDirectory() as temporary:
                config_path, _ = write_configuration(Path(temporary).resolve() / "Inputs")
                orders_path = config_path.parent / "Orders.TXT"
                orders_path.write_text("Order missing", encoding="utf-8")
                self.assertEqual(warehouse([
                    "--config", str(config_path), "--orders", str(orders_path), "--debug", "T",
                ]), 1)
                reader = OrderReader()
                with self.assertRaises(OSError):
                    reader.read_orders(config_path.parent / "Missing.TXT")
                orders_path.write_bytes(b"\xff")
                with self.assertRaises(ValueError):
                    reader.read_orders(orders_path)
                orders_path.write_text("\nOrder Unknown Purple", encoding="utf-8")
                self.assertEqual(reader.read_orders(orders_path), ((2, "Unknown", "Purple"),))
            catalog = ProductCatalog({("Model", "Blue"): ("1", "2")})
            with self.assertRaises(ValueError):
                catalog.resolve_skus("Unknown", "Blue")
            for products in (
                {"AB": ("1", "2")},
                {("Model", "Blue"): ["1", "2"]},
                {("Model", "Blue"): ("1", "0")},
                {("Model", "Blue"): ("1", "2"), (" MODEL ", " BLUE "): ("3", "4")},
            ):
                with self.assertRaises(ValueError):
                    ProductCatalog(products)
        lines = stdout.getvalue().splitlines()
        self.assertEqual(len(lines), len(set(lines)))
        emitted = Counter()
        contexts = {
            "launcher.warehouse": ("config_supplied=", "orders_supplied=", "console_level=DEBUG"),
            "order_reader.read_orders": ("path=",),
            "order_reader.parse_order": ("path=", "line=", "field_count=", "fields=", "reason=invalid_grammar"),
            "config_loader.load_config": ("supplied=", "source=", "path="),
            "config_loader._read_json": ("source=", "path="),
            "config_loader._read_csv": ("headers=", "source=", "path="),
            "product_catalog.__init__": ("supplied_product=", "normalized_product=", "sku_evidence=", "reason="),
            "product_catalog.resolve_skus": ("supplied_product=", "normalized_product=", "reason=unknown_product"),
            "product_catalog._normalize_product": ("supplied_product=", "sku_evidence=", "reason="),
            "product_catalog._validate_skus": ("supplied_product=", "normalized_product=", "sku_evidence=", "reason="),
        }
        for line in lines:
            match = re.match(r"\[DEBUG\] function=(\S+) action=(\S+) ", line)
            self.assertIsNotNone(match, line)
            label, action = match.groups()
            emitted[label, action] += 1
            self.assertNotIn("=n/a", line)
            for evidence in contexts[label]:
                self.assertIn(evidence, line)
            if label == "order_reader.read_orders":
                if action == "validated":
                    for evidence in ("byte_count=", "line_count=2", "order_count=1", "blank_count=1"):
                        self.assertIn(evidence, line)
                elif "reason=invalid_utf8" in line:
                    self.assertIn("byte_offset=0", line)
                    self.assertIn("byte_end=1", line)
                else:
                    self.assertIn("reason=read_failed", line)
                    self.assertIn("error=", line)
        expected_emissions = Counter({(label, action): count for (identity, label, action), count in expected.items()})
        expected_emissions["config_loader._read_csv", "entry"] = 3
        self.assertEqual(emitted, expected_emissions)

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
