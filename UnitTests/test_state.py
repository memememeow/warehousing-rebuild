"""Verify Step 02 constructor diagnostics; reserve composed-state smoke coverage.

Future tests will verify the five state owners, shared request identity,
collection isolation and informational replenisher readiness. Fixtures must
initialize numeric settings once before constructing mutable state.
"""

from __future__ import annotations

import ast
import io
import logging
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError
from pathlib import Path

from UnitTests import assert_callable_traces, isolated_console_logging
from warehouse.configuration.warehouse_config import WarehouseConfig
from warehouse.domain.location import Location
from warehouse.domain.order import Order
from warehouse.file_io.activity_log import ActivityLog
from warehouse.launcher import _configure_console_logging, warehouse
from warehouse.routing.route_planner import RoutePlanner


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
                locations = {"1": location, "2": location}
                planner = RoutePlanner(locations)
                # The catalog owner is still a stub; never invoke or inspect it here.
                catalog = object()
                config = WarehouseConfig(
                    Path("Translation.CSV"), Path("Traversal.CSV"), Path("Stock.CSV"),
                    catalog, locations, {location: 6}, {}, 30, 5, 4, 4, 5,
                    4, 3, 3, 1, 1, 1, "%Y%m%d%H%M",
                )
                self.assertIs(config.catalog, catalog)
                self.assertIs(config.location_map, locations)
                self.assertIs(planner.location_map, locations)
                self.assertEqual(order.id, "O0001")
                self.assertEqual(hash(location), hash(Location("A", 0, 1, 2)))
                for value, field in (
                    (location, "zone"), (order, "id"), (planner, "location_map"),
                    (config, "default_stock"),
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
            WarehouseConfig(
                Path("Translation.CSV"), Path("Traversal.CSV"), Path("Stock.CSV"),
                object(), {"1": location}, {}, {}, 30, 5, 4, 4, 5,
                4, 3, 3, 1, 1, 1, "%Y%m%d%H%M",
            )
            log = ActivityLog(Path("unused-run"), stdout)
            log.mark_incomplete("fixture", {})
            warehouse([
                "--config", "Config.JSON", "--orders", "Orders.TXT", "--debug", "T"
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
