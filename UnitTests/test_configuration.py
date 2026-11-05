"""Exercise real configuration files, immutable publication and startup boundaries."""

from __future__ import annotations

import copy
import importlib
import inspect
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path
from collections import UserDict
from collections.abc import Mapping
from typing import get_type_hints
from unittest.mock import Mock, patch

from UnitTests import assert_callable_traces, isolated_console_logging
from warehouse.configuration.config_loader import ConfigLoader
from warehouse.configuration.warehouse_config import WarehouseConfig
from warehouse.domain.location import Location, LocationMap
from warehouse.domain.product_catalog import ProductCatalog
from warehouse.domain.worker_type import WorkerType
from warehouse.launcher import _configure_console_logging, warehouse


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def write_configuration(directory: Path) -> tuple[Path, dict]:
    """Build small real inputs with mixed spelling and deliberately unsorted SKUs."""
    directory.mkdir(parents=True, exist_ok=True)
    document = {
        "files": {"translation": "Products.CSV", "location_map": "Traversal.CSV",
                  "initial_inventory": "Stock.CSV"},
        "warehouse": {"default_stock": 43, "replenishment_threshold": 0,
                      "orders_per_request": 2, "pallet_capacity_fascia": 3,
                      "truck_capacity_requests": 7},
        "employees": {"picker": [" Alice ", "Bob"], "sequencer": ["Sue"],
                      "loader": ["Bill"], "replenisher": ["Ruby"]},
        "identifiers": {"first_order_number": 7, "first_request_number": 11,
                        "first_truck_number": 13, "order_minimum_digits": 5,
                        "request_minimum_digits": 6, "truck_minimum_digits": 2,
                        "request_timestamp_format": "%Y%m%d%H%M"},
    }
    (directory / "Products.CSV").write_text(
        " Colour ,MODEL,SKU (front),sku (BACK)\nBlue,Model,001,002\nRed,Other,3,4\n",
        encoding="utf-8",
    )
    (directory / "Traversal.CSV").write_text(
        "zone,aisle,rack,level,sku\nNorth,0,0,1,002\nnORTH,0,0,0,001\n"
        "South,1,0,1,4\nSOUTH,1,0,0,3\n", encoding="utf-8",
    )
    (directory / "Stock.CSV").write_text(
        "zone,aisle,rack,level,stock\n nOrTh ,0,0,0,0\nsouth,1,0,1,6\n",
        encoding="utf-8",
    )
    path = directory / "Config.JSON"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path, document


class ConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(isolated_console_logging()).addHandler(logging.NullHandler())
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.path, self.document = write_configuration(self.root / "Mixed Case Inputs")

    def save(self, document: dict | None = None) -> None:
        self.path.write_text(json.dumps(self.document if document is None else document),
                             encoding="utf-8")

    def load(self) -> WarehouseConfig:
        return ConfigLoader().load_config(self.path)

    def assert_csv_failure(self, filename: str, contents: str | bytes, reason: str) -> None:
        path = self.path.parent / filename
        original = path.read_bytes()
        try:
            path.write_bytes(contents.encode("utf-8") if isinstance(contents, str) else contents)
            with self.assertRaisesRegex(ValueError, reason) as caught:
                self.load()
            for context in (str(self.path), filename, f"resolved={str(path)!r}"):
                self.assertIn(context, str(caught.exception))
        finally:
            path.write_bytes(original)

    def test_real_catalog_paths_zones_order_and_all_contracted_fields(self) -> None:
        config = self.load()
        self.assertIsInstance(config, WarehouseConfig)
        self.assertIs(type(config.catalog), ProductCatalog)
        self.assertEqual(config.catalog.resolve_skus(" MODEL ", "bLuE"), ("001", "002"))
        canonical = config.catalog.products[("model", "blue")]
        self.assertIs(config.catalog.resolve_skus("model", "blue"), canonical)
        with self.assertRaisesRegex(ValueError, "Unknown product"):
            config.catalog.resolve_skus("missing", "blue")
        self.assertEqual(tuple(config.location_map), ("002", "001", "4", "3"))
        self.assertEqual(tuple(config.location_map.values()), (
            Location("North", 0, 0, 1), Location("North", 0, 0, 0),
            Location("South", 1, 0, 1), Location("South", 1, 0, 0),
        ))
        self.assertEqual(dict(config.initial_stock), {
            config.location_map["001"]: 0, config.location_map["4"]: 6,
        })
        for location in config.initial_stock:
            self.assertTrue(any(location is mapped for mapped in config.location_map.values()))
        self.assertEqual(config.employees[WorkerType.PICKER], (" Alice ", "Bob"))
        self.assertTrue(all(type(role) is WorkerType for role in config.employees))
        for setting, filename in self.document["files"].items():
            self.assertEqual(getattr(config, setting + "_path"), (self.path.parent / filename).resolve())
        for section in ("warehouse", "identifiers"):
            for name, value in self.document[section].items():
                self.assertEqual(getattr(config, name), value)
        expected = {"catalog", "location_map", "initial_stock", "employees",
                    "translation_path", "location_map_path", "initial_inventory_path",
                    *self.document["warehouse"], *self.document["identifiers"]}
        self.assertEqual({field.name for field in fields(config)}, expected)

    def test_duplicate_json_keys_at_every_depth_before_overwrite(self) -> None:
        for text in (
            '{"files":{},"files":{}}', '{"files":{}," FILES ":{}}',
            '{"files":{"translation":1," Translation ":2}}',
            '{"extra":{"deep":{"Straße":1,"STRASSE":2}}}',
            '{"extra":[{"key":1,"key":2}]}',
            '{"extra":[[0,{"Key":1," key ":2}]]}',
        ):
            with self.subTest(text=text):
                self.path.write_text(text, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "duplicate JSON key") as caught:
                    self.load()
                self.assertIn(str(self.path), str(caught.exception))

    def test_valid_long_unused_json_integer_is_accepted_without_changing_settings(self) -> None:
        self.path.write_text(json.dumps(self.document)[:-1] + ',"extra":' + '9' * 5000 + '}',
                             encoding="utf-8")
        self.assertEqual(self.load().default_stock, 43)

    def test_nonstandard_json_constants_reject_at_every_depth_before_publication(self) -> None:
        for token in ("NaN", "Infinity", "-Infinity"):
            for location in (token, '{"value":' + token + '}', '[0,[{"value":' + token + '}]]'):
                text = json.dumps(self.document)[:-1] + ',"extra":' + location + '}'
                with self.subTest(token=token, location=location):
                    self.path.write_text(text, encoding="utf-8")
                    with patch("warehouse.configuration.config_loader.WarehouseConfig") as snapshot:
                        with self.assertRaises(ValueError) as caught:
                            self.load()
                        snapshot.assert_not_called()
                    self.assertIn(str(self.path), str(caught.exception))
                    self.assertIn(token, str(caught.exception))

    def test_quoted_constant_spellings_and_legitimate_numbers_are_accepted(self) -> None:
        self.document["extra"] = {"NaN": ["NaN", "Infinity", "-Infinity", 0, -2, 1.25, 3e4, None]}
        self.save()
        self.assertEqual(self.load().default_stock, 43)

    def test_recursive_key_normalization_preserves_values(self) -> None:
        def mixed_keys(value: object) -> object:
            if isinstance(value, dict):
                return {f" {key.upper()} ": mixed_keys(item) for key, item in value.items()}
            if isinstance(value, list):
                return [mixed_keys(item) for item in value]
            return value

        document = mixed_keys(self.document)
        document[" EXTRA "] = [{" Mixed KEY ": {" Straße ": "MiXeD Value"}}]
        self.save(document)
        self.assertEqual(self.load().catalog.resolve_skus("MODEL", "Blue"), ("001", "002"))
        from warehouse.configuration.config_loader import _read_json
        decoded = _read_json(self.path, str(self.path))
        self.assertEqual(decoded["extra"], [{"mixed key": {"strasse": "MiXeD Value"}}])
        self.assertEqual(decoded["files"], self.document["files"])

    def test_missing_malformed_and_undecodable_json(self) -> None:
        for text in ("", '{\n"files": }', '{"x":1} trailing', "[]", "null", "42"):
            with self.subTest(text=text):
                self.path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError) as caught:
                    self.load()
                self.assertIn(str(self.path), str(caught.exception))
                self.assertIn("JSON", str(caught.exception))
                if text not in ("[]", "null", "42"):
                    self.assertIn("line=", str(caught.exception))
                    self.assertIn("column=", str(caught.exception))
                    self.assertIsInstance(caught.exception.__cause__, json.JSONDecodeError)
        self.path.write_bytes(b"\xff")
        with self.assertRaisesRegex(ValueError, "UTF-8") as caught:
            self.load()
        self.assertIn(str(self.path), str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, UnicodeError)
        self.path.unlink()
        with self.assertRaises(OSError) as caught:
            self.load()
        self.assertIn(str(self.path), str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, OSError)

    def test_every_required_section_and_field_is_mandatory_without_defaults(self) -> None:
        for section in ("files", "warehouse", "employees", "identifiers"):
            for value in (None, [], "invalid", 1, False):
                document = copy.deepcopy(self.document)
                document[section] = value
                self.save(document)
                with self.subTest(section=section, value=value), self.assertRaises(ValueError) as caught:
                    self.load()
                self.assertIn(section, str(caught.exception))
            document = copy.deepcopy(self.document)
            del document[section]
            self.save(document)
            with self.subTest(section=section), self.assertRaises(ValueError) as caught:
                self.load()
            self.assertIn(section, str(caught.exception))
        for section in ("files", "warehouse", "identifiers"):
            for field in self.document[section]:
                document = copy.deepcopy(self.document)
                del document[section][field]
                self.save(document)
                with self.subTest(section=section, field=field), self.assertRaises(ValueError) as caught:
                    self.load()
                self.assertIn(str(self.path), str(caught.exception))
                self.assertIn(field, str(caught.exception))

    def test_all_numeric_settings_reject_wrong_types_and_ranges(self) -> None:
        for section in ("warehouse", "identifiers"):
            for field, original in self.document[section].items():
                if type(original) is not int:
                    continue
                invalid = (True, False, 1.0, 1.5, "1", None, [], {}, -1)
                if field != "replenishment_threshold":
                    invalid += (0,)
                for value in invalid:
                    document = copy.deepcopy(self.document)
                    document[section][field] = value
                    self.save(document)
                    with self.subTest(field=field, value=value), self.assertRaises(ValueError) as caught:
                        self.load()
                    self.assertIn(f"{section}.{field}", str(caught.exception))
                    self.assertIn(str(self.path), str(caught.exception))
                    self.assertIn(repr(value), str(caught.exception))
        for threshold in (43, 44):
            document = copy.deepcopy(self.document)
            document["warehouse"]["replenishment_threshold"] = threshold
            self.save(document)
            with self.assertRaisesRegex(ValueError, "0 <= replenishment_threshold < default_stock"):
                self.load()

    def test_supplied_positive_starts_and_nondefault_numbers_are_retained(self) -> None:
        for field in ("first_order_number", "first_request_number", "first_truck_number"):
            self.document["identifiers"][field] = 123
        self.document["warehouse"]["default_stock"] = 1
        self.save()
        config = self.load()
        for field in ("first_order_number", "first_request_number", "first_truck_number"):
            self.assertEqual(getattr(config, field), 123)
        self.assertEqual((config.default_stock, config.replenishment_threshold), (1, 0))

    def test_timestamp_format_is_exact(self) -> None:
        for value in (None, True, 1, [], "%Y%m%d", " %Y%m%d%H%M", "%Y%m%d%H%M "):
            document = copy.deepcopy(self.document)
            document["identifiers"]["request_timestamp_format"] = value
            self.save(document)
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "request_timestamp_format"):
                self.load()

    def test_invalid_reference_paths_preserve_source_and_supplied_text(self) -> None:
        for field in self.document["files"]:
            for value in (None, True, 4, [], {}, "", "bad\x00path"):
                document = copy.deepcopy(self.document)
                document["files"][field] = value
                self.save(document)
                with self.subTest(field=field, value=value), self.assertRaises(ValueError) as caught:
                    self.load()
                self.assertIn(f"files.{field}", str(caught.exception))
                self.assertIn(repr(value), str(caught.exception))
                self.assertIn(str(self.path), str(caught.exception))

    def test_all_csv_files_missing_and_directory_errors_have_path_context(self) -> None:
        for field, filename in self.document["files"].items():
            path = self.path.parent / filename
            original = path.read_bytes()
            path.unlink()
            for directory in (False, True):
                if directory:
                    path.mkdir()
                with self.subTest(field=field, directory=directory), self.assertRaises(OSError) as caught:
                    self.load()
                for context in (str(self.path), f"files.{field}", f"supplied={filename!r}",
                                f"resolved={str(path)!r}"):
                    self.assertIn(context, str(caught.exception))
                self.assertIsInstance(caught.exception.__cause__, OSError)
                if directory:
                    path.rmdir()
            path.write_bytes(original)

    def test_csv_header_collisions_missing_extra_headers_and_row_shapes(self) -> None:
        for filename in self.document["files"].values():
            header, *rows = (self.path.parent / filename).read_text(encoding="utf-8").splitlines()
            for bad_header in (header + "," + header.split(",")[0].upper() + " ",
                               ",".join(header.split(",")[1:]), header + ",extra"):
                with self.subTest(filename=filename, header=bad_header):
                    self.assert_csv_failure(filename, bad_header + "\n", "header")
            for row in (rows[0] + ",extra", ",".join(rows[0].split(",")[1:]), ""):
                with self.subTest(filename=filename, row=row):
                    self.assert_csv_failure(filename, header + "\n" + row + "\n", "line=2.*fields")

    def test_csv_empty_decoding_and_actual_parser_errors(self) -> None:
        for filename in self.document["files"].values():
            header = (self.path.parent / filename).read_text(encoding="utf-8").splitlines()[0]
            with self.subTest(filename=filename):
                self.assert_csv_failure(filename, "", "missing header")
                self.assert_csv_failure(filename, b"\xff", "UTF-8")
                self.assert_csv_failure(filename, header + '\n"unterminated', "CSV line=2")

    def test_duplicate_product_rows_reject_before_dictionary_overwrite(self) -> None:
        for second in ("Blue,Model,001,002", " blue , MODEL ,001,002",
                       "BLUE,model,3,4", "WEISS,STRASSE,3,4"):
            first = "Weiß,Straße,001,002" if "STRASSE" in second else "Blue,Model,001,002"
            with self.subTest(second=second):
                self.assert_csv_failure("Products.CSV", "Colour,Model,SKU (front),SKU (back)\n"
                                        + first + "\n" + second + "\n", "line=3.*collision")

    def test_sku_tokens_reject_nonpositive_and_nondecimal_values_without_trimming(self) -> None:
        for token in ("", "0", "000", "٠０", "-1", "+1", "1.0", "1e2", " 1", "1 ",
                      "1_0", "True", "False", "NaN", "²"):
            for side in ("front", "back", "map"):
                with self.subTest(token=token, side=side):
                    if side == "map":
                        self.assert_csv_failure("Traversal.CSV", "zone,aisle,rack,level,sku\n"
                                                f"North,0,0,0,{token}\n", "positive decimal string")
                    else:
                        pair = f"{token},002" if side == "front" else f"001,{token}"
                        self.assert_csv_failure("Products.CSV", "Colour,Model,SKU (front),SKU (back)\n"
                                                f"Blue,Model,{pair}\n", "positive decimal string")

    def test_unicode_long_shared_and_leading_zero_skus_keep_exact_tokens(self) -> None:
        for front, back in (("٠١", "２"), ("9" * 5000, "002"), ("001", "001"), ("01", "1")):
            path, _ = write_configuration(self.path.parent)
            (path.parent / "Products.CSV").write_text(
                f"Colour,Model,SKU (front),SKU (back)\nBlue,Model,{front},{back}\n", encoding="utf-8")
            skus = tuple(dict.fromkeys((front, back)))
            (path.parent / "Traversal.CSV").write_text(
                "zone,aisle,rack,level,sku\n" + "".join(
                    f"North,0,0,{index},{sku}\n" for index, sku in enumerate(skus)), encoding="utf-8")
            (path.parent / "Stock.CSV").write_text("zone,aisle,rack,level,stock\n", encoding="utf-8")
            with self.subTest(front=front[:10], back=back):
                self.assertEqual(self.load().catalog.resolve_skus("Model", "Blue"), (front, back))
                self.assertEqual(tuple(self.load().location_map), skus)

    def test_ambiguous_sku_and_normalized_location_collisions_reject(self) -> None:
        for row, reason in (("North,0,0,2,002", "duplicate SKU"),
                            ("nORTH,0,0,1,5", "location normalization collision"),
                            (" North ,00,0,1,5", "location normalization collision")):
            original = (self.path.parent / "Traversal.CSV").read_text(encoding="utf-8")
            with self.subTest(row=row):
                self.assert_csv_failure("Traversal.CSV", original + row + "\n", reason)

    def test_coverage_and_initial_stock_membership_and_duplicates(self) -> None:
        self.assert_csv_failure("Traversal.CSV", "zone,aisle,rack,level,sku\nNorth,0,0,0,001\n",
                                "has no location")
        for row, reason in (("Unknown,0,0,0,5", "unknown location zone"),
                            ("North,9,0,0,5", "unknown initial-stock location"),
                            ("NORTH,0,0,0,5\n North ,00,0,0,6", "duplicate initial-stock")):
            self.assert_csv_failure("Stock.CSV", "zone,aisle,rack,level,stock\n" + row + "\n", reason)

    def test_stock_and_coordinates_require_nonnegative_integers(self) -> None:
        for filename in ("Traversal.CSV", "Stock.CSV"):
            original = (self.path.parent / filename).read_text(encoding="utf-8").splitlines()
            columns = original[0].split(",")
            for field in ("aisle", "rack", "level", *(("stock",) if filename == "Stock.CSV" else ())):
                for value in ("-1", "True", "False", "0.0", "1.5", "1e2", "", "NaN"):
                    row = original[1].split(",")
                    row[columns.index(field)] = value
                    with self.subTest(filename=filename, field=field, value=value[:12]):
                        self.assert_csv_failure(filename, original[0] + "\n" + ",".join(row) + "\n",
                                                "nonnegative integer")

    def test_empty_reference_tables_and_map_size_have_no_fixed_limit(self) -> None:
        for filename in self.document["files"].values():
            path = self.path.parent / filename
            path.write_text(path.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
        config = self.load()
        self.assertEqual(dict(config.catalog.products), {})
        self.assertEqual(dict(config.location_map), {})
        self.assertEqual(dict(config.initial_stock), {})

    def test_real_catalog_instance_is_retained_at_snapshot_publication(self) -> None:
        with patch("warehouse.configuration.config_loader.WarehouseConfig", wraps=WarehouseConfig) as build:
            config = self.load()
        build.assert_called_once()
        self.assertIs(config.catalog, build.call_args.kwargs["catalog"])
        self.assertIs(type(config.catalog), ProductCatalog)
        self.assertEqual(config.catalog.resolve_skus(" Model ", "Blue"), ("001", "002"))

    def test_legacy_roles_and_ignored_fields_warn_once_and_preserve_display_order(self) -> None:
        self.document["employees"] = {f" {role.upper()}S ": names
                                       for role, names in self.document["employees"].items()}
        self.document["files"]["output_root"] = {"ignored": "does not need to be a path"}
        self.document["identifiers"]["request_timestamp_timezone"] = "Invalid/Ignored"
        self.save()
        with self.assertLogs("warehouse", level="WARNING") as captured:
            config = self.load()
        warnings = [record for record in captured.records if record.levelno == logging.WARNING]
        self.assertEqual(len(warnings), 6)
        for role in WorkerType:
            self.assertEqual(sum(f"canonical={role.value}" in record.getMessage() for record in warnings), 1)
        for field in ("files.output_root", "identifiers.request_timestamp_timezone"):
            self.assertEqual(sum(field in record.getMessage() for record in warnings), 1)
        self.assertEqual(tuple(config.employees), tuple(WorkerType))
        self.assertEqual(config.employees[WorkerType.PICKER], (" Alice ", "Bob"))
        self.assertFalse(hasattr(config, "output_root"))
        self.assertFalse(hasattr(config, "request_timestamp_timezone"))

    def test_roles_require_all_canonical_roles_and_reject_unknown_or_duplicate_aliases(self) -> None:
        for role in WorkerType:
            for change in ("missing", "duplicate", "alias_first", "unknown"):
                document = copy.deepcopy(self.document)
                if change == "missing":
                    del document["employees"][role.value]
                elif change == "duplicate":
                    document["employees"][role.value + "s"] = ["Other"]
                elif change == "alias_first":
                    document["employees"] = {role.value + "s": ["Other"], **document["employees"]}
                else:
                    document["employees"]["supervisor"] = ["Other"]
                self.save(document)
                with self.subTest(role=role, change=change), self.assertRaisesRegex(ValueError, "role"):
                    self.load()

    def test_employee_shapes_and_same_role_name_collisions(self) -> None:
        for names in ([], "Alice", None, 1, [1], [None], ["Alice", " ALICE "], ["Straße", "STRASSE"]):
            document = copy.deepcopy(self.document)
            document["employees"]["picker"] = names
            self.save(document)
            with self.subTest(names=names), self.assertRaisesRegex(ValueError, "employees.picker"):
                self.load()
        self.document["employees"]["loader"] = [" Alice "]
        self.save()
        self.assertEqual(self.load().employees[WorkerType.LOADER], (" Alice ",))

    def test_real_loading_and_rejection_precede_constants_state_orders_and_output(self) -> None:
        for invalid in ("numeric", "duplicate", "NaN", "Infinity", "-Infinity",
                        "csv", "coverage", "role", "missing"):
            write_configuration(self.path.parent)
            if invalid == "numeric":
                document = copy.deepcopy(self.document)
                document["warehouse"]["default_stock"] = True
                self.save(document)
            elif invalid == "duplicate":
                self.path.write_text('{"files":{},"FILES":{}}', encoding="utf-8")
            elif invalid == "csv":
                (self.path.parent / "Stock.CSV").write_text("zone,zone\n", encoding="utf-8")
            elif invalid == "coverage":
                (self.path.parent / "Traversal.CSV").write_text("zone,aisle,rack,level,sku\n", encoding="utf-8")
            elif invalid == "role":
                document = copy.deepcopy(self.document)
                document["employees"]["supervisor"] = ["Unknown"]
                self.save(document)
            elif invalid == "missing":
                (self.path.parent / "Products.CSV").unlink()
            else:
                self.path.write_text(json.dumps(self.document)[:-1] + ',"extra":[{"v":' + invalid + '}]}',
                                     encoding="utf-8")
            before = sorted(self.root.rglob("*"))
            contents = {path: path.read_bytes() for path in before if path.is_file()}
            with self.subTest(invalid=invalid), ExitStack() as stack:
                stdout = stack.enter_context(redirect_stdout(io.StringIO()))
                stderr = stack.enter_context(redirect_stderr(io.StringIO()))
                guards = [stack.enter_context(patch(target)) for target in (
                    "warehouse.launcher._initialize_numeric_constants",
                    "warehouse.domain.warehouse_state.WarehouseState",
                    "warehouse.file_io.order_reader.OrderReader.read_orders",
                    "warehouse.file_io.output_writer.OutputWriter.create_output_directory",
                    "warehouse.workflow.warehouse_application.WarehouseApplication.run",
                    "os.chdir", "os.mkdir",
                )]
                code = warehouse(["--config", str(self.path), "--orders", "Unvalidated.TXT"])
                self.assertEqual(code, 1)
                for guard in guards:
                    guard.assert_not_called()
                self.assertEqual(stdout.getvalue(), "")
                self.assertIn("result=INCOMPLETE", stderr.getvalue())
                self.assertIn("reason=configuration_failed", stderr.getvalue())
                self.assertNotIn("console_logging_failed", stderr.getvalue())
            self.assertEqual(sorted(self.root.rglob("*")), before)
            self.assertEqual({path: path.read_bytes() for path in contents}, contents)
            for module_name, names in (
                ("workflow.inventory", ("DEFAULT_STOCK", "REPLENISHMENT_THRESHOLD")),
                ("workflow.order_registry", ("ORDERS_PER_REQUEST", "FIRST_ORDER_NUMBER", "ORDER_MINIMUM_DIGITS",
                                             "FIRST_REQUEST_NUMBER", "REQUEST_MINIMUM_DIGITS")),
                ("workflow.sequencing_workflow", ("PALLET_CAPACITY_FASCIA",)),
                ("workflow.loading_workflow", ("FIRST_TRUCK_NUMBER", "TRUCK_MINIMUM_DIGITS")),
                ("domain.truck", ("CAPACITY_REQUESTS",)),
            ):
                module = importlib.import_module("warehouse." + module_name)
                # Unpublished constants now actively reject reads, including
                # hasattr. Inspect bindings without invoking the read guard.
                self.assertTrue(all(name not in vars(module) for name in names))

    def test_fresh_process_paths_and_independent_configurations_in_all_debug_modes(self) -> None:
        launch = self.root / "Other Launch"
        launch.mkdir()
        orders = self.path.parent / "orders.txt"
        orders.write_text("Order Model Blue\nOrder Unknown Purple", encoding="utf-8")
        before = sorted(self.root.rglob("*"))
        for absolute in (False, True):
            document = copy.deepcopy(self.document)
            if absolute:
                document["files"] = {key: str((self.path.parent / value).resolve())
                                     for key, value in document["files"].items()}
                document["warehouse"]["default_stock"] = 77
            self.save(document)
            for directory in (self.root, launch):
                for supplied in (os.path.relpath(self.path, directory), str(self.path)):
                    verified = subprocess.run(
                        [sys.executable, "-c",
                         "import json,sys; from pathlib import Path; "
                         "from warehouse.configuration.config_loader import ConfigLoader; "
                         "c=ConfigLoader().load_config(Path(sys.argv[1])); "
                         "print(json.dumps({'default_stock':c.default_stock,"
                         "'paths':[str(c.translation_path),str(c.location_map_path),str(c.initial_inventory_path)]}))",
                         supplied], cwd=directory,
                        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
                        capture_output=True, text=True, check=False,
                    )
                    self.assertEqual(verified.returncode, 0, verified.stderr)
                    self.assertEqual(json.loads(verified.stdout), {
                        "default_stock": document["warehouse"]["default_stock"],
                        "paths": [str(self.path.parent / filename) for filename in self.document["files"].values()],
                    })
                    for debug in ([], ["--debug", "F"], ["--debug", "T"]):
                        with self.subTest(absolute=absolute, directory=directory, supplied=supplied, debug=debug):
                            result = subprocess.run(
                                [sys.executable, str(PROJECT_ROOT / "warehouse"), "--config", supplied,
                                 "--orders", str(orders), *debug], cwd=directory,
                                capture_output=True, text=True, check=False,
                            )
                            self.assertEqual(result.returncode, 1, result.stderr)
                            self.assertIn("reason=pending_run_folder", result.stderr)
                            self.assertNotIn("[INFO]", result.stdout + result.stderr)
                            self.assertNotIn("default_stock", result.stdout)
                            if "T" in debug:
                                assert_callable_traces(self, result.stdout, (
                                    "launcher.warehouse", "config_loader.load_config",
                                    "config_loader._read_json", "config_loader._read_csv",
                                ))
                                for filename in self.document["files"].values():
                                    self.assertIn(str(self.path.parent / filename), result.stdout)
                            else:
                                self.assertEqual(result.stdout, "")
                            self.assertEqual(sorted(self.root.rglob("*")), before)

    def test_json_debug_values_cannot_change_launcher_console_selection(self) -> None:
        self.document["debug"] = "T"
        self.document["warehouse"]["debug"] = "T"
        self.save()
        self.assertFalse(hasattr(self.load(), "debug"))
        orders = self.path.parent / "orders.txt"
        orders.write_text("Order Model Blue", encoding="utf-8")
        for options, expected in (([], logging.INFO), (["--debug", "F"], logging.INFO),
                                  (["--debug", "T"], logging.DEBUG)):
            code = (
                "import io,logging,sys; from contextlib import redirect_stdout,redirect_stderr; "
                "from warehouse.launcher import warehouse; out,err=io.StringIO(),io.StringIO(); "
                "\nwith redirect_stdout(out),redirect_stderr(err): result=warehouse(sys.argv[1:])"
                f"\nassert result==1; assert logging.getLogger('warehouse').level=={expected}; "
                f"assert bool(out.getvalue())=={expected == logging.DEBUG}; "
                "assert 'reason=pending_run_folder' in err.getvalue()"
            )
            with self.subTest(options=options):
                result = subprocess.run([sys.executable, "-c", code, "--config", str(self.path),
                                         "--orders", str(orders), *options],
                                        capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class ConfigurationImmutabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(isolated_console_logging())

    def test_location_constructor_rejects_invalid_coordinates(self) -> None:
        for coordinate in ("aisle", "rack", "level"):
            for value in (-1, True, False, 0.0, 1.5, "1", None):
                arguments = {"zone": "North", "aisle": 0, "rack": 0, "level": 0}
                arguments[coordinate] = value
                with self.subTest(coordinate=coordinate, value=value):
                    with self.assertRaisesRegex(ValueError, coordinate):
                        Location(**arguments)

    def make_snapshot(self) -> tuple[WarehouseConfig, dict, dict, dict, list]:
        location = Location("North", 0, 0, 0)
        locations = UserDict({"001": location})
        stock = UserDict({location: 6})
        names = [" Alice ", "Bob"]
        employees = UserDict({WorkerType.PICKER: names})
        catalog = ProductCatalog({("Model", "Blue"): ("001", "001")})
        config = WarehouseConfig(
            Path("Products.CSV"), Path("Traversal.CSV"), Path("Stock.CSV"),
            catalog, locations, stock, employees, 43, 0, 2, 3, 7,
            5, 6, 2, 7, 11, 13, "%Y%m%d%H%M",
        )
        self.assertIs(config.catalog, catalog)
        return config, locations, stock, employees, names

    def test_source_mapping_and_employee_list_mutation_cannot_change_snapshot(self) -> None:
        config, locations, stock, employees, names = self.make_snapshot()
        location = config.location_map["001"]
        for source, frozen in ((locations, config.location_map), (stock, config.initial_stock),
                               (employees, config.employees)):
            self.assertIsNot(source, frozen)
        names[:] = ["Changed"]
        locations["001"] = Location("Other", 9, 9, 9)
        stock[location] = 99
        employees[WorkerType.LOADER] = ["Added"]
        for source in (locations, stock, employees):
            source.clear()
        self.assertEqual(dict(config.location_map), {"001": location})
        self.assertEqual(dict(config.initial_stock), {location: 6})
        self.assertEqual(dict(config.employees), {WorkerType.PICKER: (" Alice ", "Bob")})
        self.assertIs(config.location_map["001"], location)

    def test_exposed_mappings_and_nested_reference_tables_reject_mutation(self) -> None:
        config, *_ = self.make_snapshot()
        for mapping in (config.location_map, config.initial_stock, config.employees, config.catalog.products):
            before = dict(mapping)
            key = next(iter(mapping))
            value = mapping[key]
            with self.subTest(mapping=type(key)):
                with self.assertRaises(TypeError):
                    mapping[key] = value
                with self.assertRaises(TypeError):
                    del mapping[key]
                self.assertEqual(dict(mapping), before)
        with self.assertRaises(TypeError):
            config.employees[WorkerType.PICKER][0] = "Changed"

    def test_every_snapshot_and_location_field_is_frozen_and_slotted(self) -> None:
        config, *_ = self.make_snapshot()
        location = config.location_map["001"]
        for value in (config, location):
            self.assertTrue(type(value).__dataclass_params__.frozen)
            self.assertFalse(hasattr(value, "__dict__"))
            self.assertEqual(set(type(value).__slots__), {field.name for field in fields(value)})
            for field in fields(value):
                original = getattr(value, field.name)
                with self.subTest(owner=type(value), field=field.name):
                    with self.assertRaises(FrozenInstanceError):
                        setattr(value, field.name, "Changed")
                    with self.assertRaises(FrozenInstanceError):
                        delattr(value, field.name)
                    self.assertIs(getattr(value, field.name), original)
        self.assertEqual(hash(location), hash(Location("North", 0, 0, 0)))

    def test_public_signatures_fields_and_location_alias_remain_exact(self) -> None:
        self.assertEqual(tuple(inspect.signature(Location).parameters), ("zone", "aisle", "rack", "level"))
        self.assertEqual(LocationMap.__value__, Mapping[str, Location])
        self.assertEqual(get_type_hints(Location), {"zone": str, "aisle": int, "rack": int, "level": int})
        self.assertEqual(tuple(inspect.signature(ConfigLoader.load_config).parameters), ("self", "config_path"))
        self.assertEqual(get_type_hints(ConfigLoader.load_config), {"config_path": Path, "return": WarehouseConfig})
        self.assertEqual(tuple(inspect.signature(WarehouseConfig).parameters),
                         tuple(field.name for field in fields(WarehouseConfig)))


class ConfigurationDiagnosticTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = self.enterContext(isolated_console_logging())
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.path, self.document = write_configuration(self.root / "Inputs")


    def test_real_loader_retains_file_entries_without_routine_traces_or_duplicate_messages(self) -> None:
        for level in (logging.INFO, logging.DEBUG):
            with self.subTest(level=level), redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                _configure_console_logging(level)
                config = ConfigLoader().load_config(self.path)
                self.assertEqual(config.catalog.resolve_skus("Model", "Blue"), ("001", "002"))
                self.assertEqual(err.getvalue(), "")
                if level == logging.INFO:
                    self.assertEqual(out.getvalue(), "")
                    continue
                output = out.getvalue()
                assert_callable_traces(self, output, (
                    "config_loader.load_config", "config_loader._read_json", "config_loader._read_csv",
                ))
                for evidence in (str(self.path), "Products.CSV", "Traversal.CSV", "Stock.CSV",
                                 "headers=", "supplied=", "resolved=", "path="):
                    self.assertIn(evidence, output)
                lines = output.splitlines()
                self.assertEqual(len(lines), 5)
                self.assertEqual(len(lines), len(set(lines)))
                for line in lines:
                    self.assertTrue(line.startswith("[DEBUG] "))
                    self.assertIn("action=entry", line)
                    self.assertNotIn("=n/a", line)
                    self.assertNotIn("row_count=", line)
                    self.assertNotIn("line_number=", line)
                self.assertEqual(sum("function=config_loader._read_csv " in line for line in lines), 3)

    def test_suppressed_loader_and_snapshot_debug_never_formats_evidence(self) -> None:
        class UnformattablePath(type(Path())):
            def __repr__(self) -> str:
                raise AssertionError("suppressed path repr")

        class UnformattableNames(list):
            def __repr__(self) -> str:
                raise AssertionError("suppressed names repr")

        out = Mock()
        out.write.side_effect = AssertionError("suppressed stdout write")
        with patch("sys.stdout", out):
            _configure_console_logging(logging.INFO)
            config = ConfigLoader().load_config(UnformattablePath(self.path))
            replace(config, employees={WorkerType.PICKER: UnformattableNames(["Alice"])})
        out.write.assert_not_called()
        out.flush.assert_not_called()


    def test_retained_file_entry_transport_errors_are_not_relabelled_as_input_errors(self) -> None:
        for phase in ("load_config", "_read_json", "_read_csv"):
            for failure in (ValueError("file diagnostic failed"),
                            OSError("file diagnostic failed"),
                            json.JSONDecodeError("syntax-shaped transport failure", "{}", 0)):
                with self.subTest(phase=phase, error_type=type(failure)):
                    captured, stderr = io.StringIO(), io.StringIO()
                    stream = Mock(wraps=captured)
                    def write(text: str) -> int:
                        if f"function=config_loader.{phase} action=entry " in text:
                            raise failure
                        return captured.write(text)
                    stream.write.side_effect = write
                    with patch("sys.stdout", stream), redirect_stderr(stderr), patch.object(logging, "raiseExceptions", False):
                        _configure_console_logging(logging.DEBUG)
                        with self.assertRaises(type(failure)) as caught:
                            ConfigLoader().load_config(self.path)
                        self.assertIs(caught.exception, failure)
                        self.assertIsNone(failure.__cause__)
                        self.assertTrue(failure.warehouse_logging_failure)
                        self.assertEqual(warehouse(["--config", str(self.path), "--orders", "Unvalidated.TXT", "--debug", "T"]), 1)
                    self.assertIn("reason=console_logging_failed", stderr.getvalue())
                    self.assertNotIn("reason=configuration_failed", stderr.getvalue())
                    self.assertNotIn("configuration validated", stderr.getvalue())

    def test_actual_loader_stream_write_flush_short_and_closed_failures_propagate(self) -> None:
        for phase in ("config_loader._read_csv", "config_loader._read_json", "config_loader.load_config"):
            for operation in ("write", "flush", "short", "closed"):
                with self.subTest(phase=phase, operation=operation):
                    captured = io.StringIO()
                    stream = Mock(wraps=captured)
                    failure = OSError("loader transport " + operation)
                    failed = False
                    def write(text: str) -> int:
                        nonlocal failed
                        if phase in text:
                            failed = True
                            if operation == "closed":
                                captured.close()
                            elif operation == "write":
                                raise failure
                            elif operation == "short":
                                return 1
                        return captured.write(text)
                    def flush() -> None:
                        if failed and operation == "flush":
                            raise failure
                        captured.flush()
                    stream.write.side_effect = write
                    stream.flush.side_effect = flush
                    with patch("sys.stdout", stream), patch.object(logging, "raiseExceptions", False):
                        _configure_console_logging(logging.DEBUG)
                        with self.assertRaises(ValueError if operation == "closed" else OSError) as caught:
                            ConfigLoader().load_config(self.path)
                        if operation in ("write", "flush"):
                            self.assertIs(caught.exception, failure)
                        self.assertTrue(getattr(caught.exception, "warehouse_logging_failure", False))
                    _configure_console_logging(logging.INFO)

    def test_warning_transport_failure_stops_before_snapshot_and_uses_direct_fallback(self) -> None:
        self.document["files"]["output_root"] = "Ignored"
        self.path.write_text(json.dumps(self.document), encoding="utf-8")
        captured = io.StringIO()
        stream = Mock(wraps=captured)
        def write(text: str) -> int:
            if text.startswith("[WARNING]"):
                raise OSError("warning transport failed")
            return captured.write(text)
        stream.write.side_effect = write
        with patch("sys.stderr", stream), redirect_stdout(io.StringIO()), patch.object(logging, "raiseExceptions", False):
            with patch("warehouse.configuration.config_loader.WarehouseConfig", wraps=WarehouseConfig) as build:
                self.assertEqual(warehouse(["--config", str(self.path), "--orders", "Unvalidated.TXT"]), 1)
                build.assert_not_called()
        self.assertIn("reason=console_logging_failed", captured.getvalue())
        self.assertNotIn("reason=configuration_failed", captured.getvalue())
