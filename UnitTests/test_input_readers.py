"""Verify CLI, product lookup and canonical roles alongside real configuration.

Configuration cases in test_configuration cover launch-relative and config-relative
references, absolute inputs, missing files, malformed JSON and CSVs, mixed case,
normalization collisions, SKU coverage and employee roles. Future OrderReader
cases must check grammar and preserved order source context.

Launcher cases cover omitted --debug (default F), explicit T/F, lowercase
and other invalid values, missing values and help documenting the default.
Argument rejection and help must precede file reads, state construction and
run-folder creation. Debug verbosity adds no WarehouseConfig field.
"""

from __future__ import annotations

import inspect
import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Mapping
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import get_type_hints
from unittest.mock import patch

from UnitTests import assert_callable_traces, isolated_console_logging
from UnitTests.test_configuration import write_configuration
from warehouse.configuration.warehouse_config import WarehouseConfig
from warehouse.domain.product_catalog import ProductCatalog
from warehouse.domain.worker_type import WorkerType
from warehouse.launcher import _parse_arguments, warehouse


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ProductCatalogTests(unittest.TestCase):
    """Exercise the real lookup without implementing configuration or acceptance."""

    def setUp(self) -> None:
        self.enterContext(isolated_console_logging())

    def test_public_signatures_match_the_catalog_contract(self) -> None:
        for method, parameters, annotations in (
            (ProductCatalog.__init__, ("self", "products"), {
                "products": Mapping[tuple[str, str], tuple[str, str]],
                "return": type(None),
            }),
            (ProductCatalog.resolve_skus, ("self", "model", "colour"), {
                "model": str, "colour": str, "return": tuple[str, str],
            }),
        ):
            with self.subTest(method=method.__name__):
                signature = inspect.signature(method)
                self.assertEqual(tuple(signature.parameters), parameters)
                self.assertTrue(all(
                    value.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
                    and value.default is inspect.Parameter.empty
                    for value in signature.parameters.values()
                ))
                self.assertEqual(get_type_hints(method), annotations)

    def test_construction_and_lookup_share_strip_casefold_normalization(self) -> None:
        for model in ("Straße", " STRASSE ", "\tstrasse\n"):
            for colour in ("Weiß", " WEISS ", "\tweiss\n"):
                with self.subTest(model=model, colour=colour):
                    catalog = ProductCatalog({(model, colour): ("001", "020")})
                    self.assertEqual(dict(catalog.products), {("strasse", "weiss"): ("001", "020")})
                    for lookup in ((model, colour), (" STRAẞE ", " wEiSs "), ("strasse", "weiss")):
                        self.assertEqual(catalog.resolve_skus(*lookup), ("001", "020"))

    def test_distinct_source_keys_cannot_overwrite_a_normalized_pair(self) -> None:
        for first, second in (
            (("Model", "Blue"), ("model", "blue")),
            (("Model", "Blue"), (" Model ", " Blue\t")),
            (("Straße", "Weiß"), ("STRASSE", "WEISS")),
        ):
            for second_skus in (("1", "2"), ("3", "4")):
                products = {first: ("1", "2"), second: second_skus}
                before = products.copy()
                with self.subTest(first=first, second=second, skus=second_skus):
                    with self.assertRaisesRegex(ValueError, "collision"):
                        ProductCatalog(products)
                    self.assertEqual(products, before)

    def test_front_back_strings_and_canonical_tuple_are_preserved(self) -> None:
        for pair in (("0009", "001"), ("1", "1"), ("01", "1"),
                     ("9" * 5000, "2"), ("٠١", "２")):
            with self.subTest(pair=pair):
                catalog = ProductCatalog({("Model", "Blue"): pair})
                canonical = catalog.products[("model", "blue")]
                resolved = catalog.resolve_skus(" MODEL ", "bLuE")
                self.assertIs(type(resolved), tuple)
                self.assertEqual(len(resolved), 2)
                self.assertIs(resolved, canonical)
                self.assertEqual(resolved, pair)
                self.assertTrue(all(isinstance(sku, str) for sku in resolved))
                self.assertIs(catalog.resolve_skus("model", "blue"), canonical)

    def test_each_sku_must_be_a_positive_decimal_string(self) -> None:
        invalid = ("", "0", "000", "٠０", "-1", "+1", "1.0", "1e2", " 1", "1 ",
                   "1_0", "0x1", "²", "Ⅳ", 1, 1.5, True, False, None)
        for sku in invalid:
            for pair in ((sku, "2"), ("1", sku)):
                products = {("Model", "Blue"): pair}
                before = products.copy()
                with self.subTest(pair=pair):
                    with self.assertRaisesRegex(ValueError, "positive decimal string"):
                        ProductCatalog(products)
                    self.assertEqual(products, before)

    def test_sku_data_requires_one_front_and_one_back_in_a_tuple(self) -> None:
        for pair in ((), ("1",), ("1", "2", "3"), "12", ["1", "2"], None):
            with self.subTest(pair=pair), self.assertRaises(ValueError):
                ProductCatalog({("Model", "Blue"): pair})

    def test_product_keys_require_a_model_colour_string_pair(self) -> None:
        for key in ((), ("Model",), ("Model", "Blue", "extra"), "AB", (1, "Blue"), ("Model", None)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                ProductCatalog({key: ("1", "2")})

    def test_unknown_pairs_raise_value_error_without_changing_the_catalog(self) -> None:
        catalog = ProductCatalog({("Model", "Blue"): ("1", "2")})
        mapping = catalog.products
        canonical = catalog.resolve_skus("model", "blue")
        before = dict(mapping)
        for model, colour in (("missing", "Blue"), ("Model", "missing"), ("?", "?")):
            with self.subTest(model=model, colour=colour):
                with self.assertRaisesRegex(ValueError, "[Uu]nknown product") as caught:
                    catalog.resolve_skus(model, colour)
                self.assertIn(model, str(caught.exception))
                self.assertIn(colour, str(caught.exception))
                self.assertIs(catalog.products, mapping)
                self.assertEqual(dict(mapping), before)
                self.assertIs(catalog.resolve_skus("model", "blue"), canonical)

    def test_empty_catalog_rejects_lookup(self) -> None:
        catalog = ProductCatalog({})
        self.assertEqual(dict(catalog.products), {})
        with self.assertRaises(ValueError):
            catalog.resolve_skus("Model", "Blue")

    def test_normalization_adds_no_other_product_restrictions(self) -> None:
        products = {
            ("", ""): ("1", "2"),
            ("Model  X", "Blue"): ("3", "4"),
            ("Café", "Blue"): ("5", "6"),
            ("Cafe\u0301", "Blue"): ("7", "8"),
        }
        catalog = ProductCatalog(products)
        for pair, skus in products.items():
            self.assertEqual(catalog.resolve_skus(*pair), skus)
        with self.assertRaises(ValueError):
            catalog.resolve_skus("Model X", "Blue")


class CanonicalRoleTests(unittest.TestCase):
    """Exercise canonical JSON keys; ConfigLoader normalization belongs to Step 05."""

    def test_canonical_employee_keys_convert_and_lookup_by_enum(self) -> None:
        saved = {
            "picker": ["Alice"], "sequencer": ["Bob"],
            "loader": ["Carol"], "replenisher": ["Dave"],
        }
        decoded = json.loads(json.dumps(saved))
        employees = {WorkerType(role): tuple(names) for role, names in decoded.items()}
        self.assertTrue(all(type(role) is WorkerType for role in employees))
        for role, names in saved.items():
            with self.subTest(role=role):
                canonical = WorkerType(role)
                self.assertIs(WorkerType[canonical.name], canonical)
                self.assertEqual(employees[canonical], tuple(names))
        self.assertEqual(json.loads(json.dumps(employees)), saved)
        self.assertEqual(decoded, saved)

    def test_worker_type_rejects_aliases_and_unvalidated_roles(self) -> None:
        unsupported = ("pickers", "sequencers", "loaders", "replenishers",
                       "supervisor", "worker", "", None, 1, True)
        for value in (*unsupported, *(member.name for member in WorkerType),
                      *(f" {member.value} " for member in WorkerType)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                WorkerType(value)

    def test_configuration_contract_uses_canonical_enum_keys(self) -> None:
        self.assertEqual(
            get_type_hints(WarehouseConfig)["employees"],
            Mapping[WorkerType, tuple[str, ...]],
        )


class LauncherTests(unittest.TestCase):
    """Protect argument validation, path handling and the directory bootstrap."""

    def setUp(self) -> None:
        self.enterContext(isolated_console_logging())

    def test_debug_default_and_explicit_choices(self) -> None:
        for options, expected in (
            ([], "F"), (["--debug", "F"], "F"), (["--debug", "T"], "T")
        ):
            with self.subTest(options=options):
                arguments = _parse_arguments(
                    ["--config", "Config.JSON", "--orders", "Orders.TXT", *options]
                )
                self.assertEqual(arguments.debug, expected)

    def test_parser_preserves_supplied_path_text(self) -> None:
        arguments = _parse_arguments(
            ["--config", "./Input Files/../Config.JSON", "--orders", "./Orders.TXT"]
        )
        self.assertEqual(arguments.config, "./Input Files/../Config.JSON")
        self.assertEqual(arguments.orders, "./Orders.TXT")

    def test_help_and_argument_errors_stop_before_startup(self) -> None:
        required = ["--config", "Config.JSON", "--orders", "Orders.TXT"]
        cases = [
            (["--help"], 0),
            ([], 2),
            (["--config", "Config.JSON"], 2),
            (["--orders", "Orders.TXT"], 2),
            (["--config"], 2),
            ([*required, "--orders"], 2),
            ([*required, "--debug"], 2),
            ([*required, "--unknown"], 2),
        ]
        cases.extend(
            ([*required, "--debug", token], 2)
            for token in ("t", "f", "True", "False", "1", "", "TF")
        )
        boundaries = (
            "builtins.open",
            "io.open",
            "os.mkdir",
            "os.chdir",
            "warehouse.launcher.Path.cwd",
            "warehouse.launcher.Path.resolve",
            "warehouse.launcher._configure_console_logging",
            "warehouse.configuration.config_loader.ConfigLoader.load_config",
            "warehouse.file_io.order_reader.OrderReader.read_orders",
            "warehouse.file_io.output_writer.OutputWriter.create_output_directory",
            "warehouse.domain.warehouse_state.WarehouseState",
            "warehouse.workflow.warehouse_application.WarehouseApplication.run",
        )
        for argv, expected in cases:
            with self.subTest(argv=argv), ExitStack() as stack:
                mocks = [stack.enter_context(patch(target)) for target in boundaries]
                stdout = stack.enter_context(redirect_stdout(io.StringIO()))
                stderr = stack.enter_context(redirect_stderr(io.StringIO()))
                with self.assertRaises(SystemExit) as caught:
                    warehouse(argv)
                self.assertEqual(caught.exception.code, expected)
                for boundary in mocks:
                    boundary.assert_not_called()
                if expected == 0:
                    self.assertIn("--config PATH", stdout.getvalue())
                    self.assertIn("--orders PATH", stdout.getvalue())
                    self.assertIn("--debug {T,F}", stdout.getvalue())
                    self.assertIn("default: F", " ".join(stdout.getvalue().split()))
                    self.assertEqual(stderr.getvalue(), "")
                else:
                    self.assertEqual(stdout.getvalue(), "")
                    self.assertIn("error:", stderr.getvalue())

    def test_valid_startup_captures_launch_directory_once(self) -> None:
        original_directory = Path.cwd()
        with tempfile.TemporaryDirectory() as temporary:
            launch_directory = Path(temporary).resolve()
            config, _ = write_configuration(launch_directory / "Input Files")
            with (
                patch("warehouse.launcher.Path.cwd", return_value=launch_directory) as cwd,
                patch("os.chdir") as chdir,
                patch("builtins.open", wraps=open) as open_file,
                patch("io.open", wraps=io.open) as path_open,
                patch("os.mkdir") as mkdir,
                redirect_stderr(io.StringIO()) as stderr,
            ):
                result = warehouse([
                    "--config", "./Input Files/../Input Files/Config.JSON", "--orders", "./Orders.TXT"
                ])
            self.assertIn("supplied='./Input Files/../Input Files/Config.JSON'", stderr.getvalue())
            self.assertIn(f"resolved={str(config)!r}", stderr.getvalue())
            self.assertIn(f"resolved={str(launch_directory / 'Orders.TXT')!r}", stderr.getvalue())
            self.assertIn("configuration validated", stderr.getvalue())
        self.assertEqual(result, 1)
        cwd.assert_called_once_with()
        for boundary in (chdir, open_file, mkdir):
            boundary.assert_not_called()
        self.assertEqual(path_open.call_count, 4)
        self.assertEqual(Path.cwd(), original_directory)

    def test_executable_paths_and_no_artifacts_from_different_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture_root = Path(temporary).resolve()
            inputs = fixture_root / "Input Files"
            config, _ = write_configuration(inputs)
            original_config = config.read_text(encoding="utf-8")
            orders = inputs / "Orders.TXT"
            orders.write_text("Intentionally unprocessed orders", encoding="utf-8")
            launch_directory = fixture_root / "Other Launch"
            launch_directory.mkdir()
            before = sorted(fixture_root.rglob("*"))
            for launch in (fixture_root, launch_directory):
                relative_config = os.path.relpath(config, launch)
                relative_orders = os.path.relpath(orders, launch)
                cases = [(["--help"], 0), (["--debug", "invalid"], 2)]
                for config_text, orders_text in (
                    (relative_config, relative_orders),
                    (str(config), str(orders)),
                ):
                    for debug in ([], ["--debug", "F"], ["--debug", "T"]):
                        cases.append(
                            (["--config", config_text, "--orders", orders_text, *debug], 1)
                        )
                for argv, expected in cases:
                    with self.subTest(launch=launch, argv=argv):
                        result = subprocess.run(
                            [sys.executable, str(PROJECT_ROOT / "warehouse"), *argv],
                            cwd=launch,
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        self.assertEqual(result.returncode, expected, result.stderr)
                        if expected == 1:
                            if "T" in argv:
                                lines = result.stdout.splitlines()
                                self.assertTrue(all(line.startswith("[DEBUG] ") for line in lines))
                                assert_callable_traces(self, result.stdout, (
                                    "warehouse.launcher.warehouse",
                                    "warehouse.launcher._parse_arguments",
                                ))
                            else:
                                self.assertEqual(result.stdout, "")
                            self.assertEqual(len(result.stderr.splitlines()), 1)
                            self.assertTrue(result.stderr.startswith("[ERROR] "))
                            self.assertIn(
                                "simulation startup is not implemented", result.stderr
                            )
                            self.assertIn(f"supplied={argv[1]!r}", result.stderr)
                            self.assertIn(f"resolved={str(config)!r}", result.stderr)
                            self.assertIn(f"supplied={argv[3]!r}", result.stderr)
                            self.assertIn(f"resolved={str(orders)!r}", result.stderr)
                        self.assertEqual(sorted(fixture_root.rglob("*")), before)
            self.assertEqual(
                config.read_text(encoding="utf-8"),
                original_config,
            )
            self.assertEqual(
                orders.read_text(encoding="utf-8"), "Intentionally unprocessed orders"
            )

    def test_bootstrap_forwards_arguments_and_exit_result_without_chdir(self) -> None:
        argv = [
            "warehouse", "--config", "Config.JSON", "--orders", "Orders.TXT", "--debug", "T"
        ]
        original_directory = Path.cwd()
        with (
            patch("warehouse.launcher.warehouse", return_value=17) as launch,
            patch.object(sys, "argv", argv),
            patch.object(sys, "path", sys.path.copy()),
            patch("os.chdir") as chdir,
        ):
            with self.assertRaises(SystemExit) as caught:
                runpy.run_path(
                    str(PROJECT_ROOT / "warehouse" / "__main__.py"), run_name="__main__"
                )
            self.assertEqual(caught.exception.code, 17)
            launch.assert_called_once_with(argv[1:])
            self.assertEqual(sys.path[0], str(PROJECT_ROOT))
            chdir.assert_not_called()
        self.assertEqual(Path.cwd(), original_directory)


# TODO (step 07): Add real order-reader grammar and source-context cases.
