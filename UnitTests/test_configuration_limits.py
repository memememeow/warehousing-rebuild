"""Regress arbitrary-size configuration values and concrete fallback warnings."""

from __future__ import annotations

import copy
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from UnitTests import isolated_console_logging
from UnitTests.test_configuration import PROJECT_ROOT, write_configuration
from warehouse.configuration.config_loader import ConfigLoader, _read_json
from warehouse.file_io.activity_log import ActivityLog
from warehouse.domain.location import Location
from warehouse.routing.route_planner import RoutePlanner
from warehouse.launcher import _configure_console_logging, warehouse


class ConfigurationLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(isolated_console_logging())
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.path, self.document = write_configuration(self.root / "Inputs")
        self.orders = self.path.parent / "orders.txt"
        self.orders.write_text("Order Model Blue", encoding="utf-8")
        self.limit = sys.get_int_max_str_digits()

    def write_large_values(self, digits: int) -> tuple[str, int]:
        token = "9" * digits
        document = copy.deepcopy(self.document)
        for section in ("warehouse", "identifiers"):
            for field, value in document[section].items():
                if type(value) is int:
                    document[section][field] = "LARGE_INTEGER"
        document["warehouse"]["replenishment_threshold"] = "LARGE_THRESHOLD"
        document["extra"] = [{"nested": ["LARGE_INTEGER", {"negative": "NEGATIVE_INTEGER"}]}]
        text = json.dumps(document).replace('"LARGE_INTEGER"', token)
        text = text.replace('"NEGATIVE_INTEGER"', "-" + token)
        text = text.replace('"LARGE_THRESHOLD"', "8" + "9" * (digits - 1))
        self.path.write_text(text, encoding="utf-8")
        for filename in ("Traversal.CSV", "Stock.CSV"):
            path = self.path.parent / filename
            lines = path.read_text(encoding="utf-8").splitlines()
            rows = [line.split(",") for line in lines[1:]]
            for row in rows:
                row[1:4] = [token, token, token if row[3] == "1" else token[:-1] + "8"]
                if filename == "Stock.CSV":
                    row[4] = token
            path.write_text(lines[0] + "\n" + "".join(",".join(row) + "\n" for row in rows), encoding="utf-8")
        return token, 10 ** digits - 1

    def test_all_numeric_classes_accept_exact_large_values_in_real_cli_modes(self) -> None:
        for digits in (4300, 4301, 5000):
            write_configuration(self.path.parent)
            self.write_large_values(digits)
            before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
            for options in ([], ["--debug", "F"], ["--debug", "T"]):
                code = textwrap.dedent(f"""
                    import io, sys
                    from contextlib import redirect_stdout, redirect_stderr
                    from pathlib import Path
                    from warehouse.configuration.config_loader import ConfigLoader, _read_json
                    from warehouse.launcher import warehouse, _numeric_initialization_state
                    path, orders = map(Path, sys.argv[1:3])
                    expected = 10 ** {digits} - 1
                    limit = sys.get_int_max_str_digits()
                    out, err = io.StringIO(), io.StringIO()
                    with redirect_stdout(out), redirect_stderr(err):
                        result = warehouse(['--config', str(path), '--orders', str(orders), *{options!r}])
                        config = ConfigLoader().load_config(path)
                        decoded = _read_json(path, str(path))
                    assert result == 1 and 'reason=pending_simulation' in err.getvalue()
                    assert 'configuration_failed' not in err.getvalue()
                    assert 'console_logging_failed' not in err.getvalue()
                    fields = {tuple(field for section in ('warehouse', 'identifiers') for field, value in self.document[section].items() if type(value) is int)!r}
                    for field in fields:
                        actual = getattr(config, field)
                        target = 9 * 10 ** ({digits} - 1) - 1 if field == 'replenishment_threshold' else expected
                        assert type(actual) is int and actual == target, field
                    assert all(type(value) is int for value in _numeric_initialization_state().values())
                    assert decoded['extra'][0]['nested'][0] == expected
                    assert decoded['extra'][0]['nested'][1]['negative'] == -expected
                    assert tuple(config.location_map) == ('002', '001', '4', '3')
                    assert config.catalog.resolve_skus('Model', 'Blue') == ('001', '002')
                    for location in config.location_map.values():
                        assert location.aisle == expected and location.rack == expected
                        assert location.level in (expected, expected - 1)
                    for location, stock in config.initial_stock.items():
                        assert stock == expected
                        assert any(location is item for item in config.location_map.values())
                    if 'T' in {options!r}:
                        for evidence in ('function=config_loader.load_config action=entry',
                                         'function=config_loader._read_json action=entry',
                                         'function=config_loader._read_csv action=entry', 'path='):
                            assert evidence in out.getvalue(), evidence
                        for removed in ('action=returned', 'token=', 'settings=', '=n/a'):
                            assert removed not in out.getvalue(), removed
                    else: assert out.getvalue() == ''
                    assert '[INFO]' not in out.getvalue() + err.getvalue()
                    assert sys.get_int_max_str_digits() == limit
                """)
                with self.subTest(digits=digits, options=options):
                    result = subprocess.run([sys.executable, '-c', code, str(self.path), str(self.orders)],
                                            cwd=self.root, env={**os.environ, 'PYTHONPATH': str(PROJECT_ROOT)},
                                            capture_output=True, text=True, check=False)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual({path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}, before)
                    self.assertEqual(sys.get_int_max_str_digits(), self.limit)

    def test_large_rejections_keep_full_observed_evidence_and_required_range(self) -> None:
        token = "9" * 5000
        for options in ([], ["--debug", "F"], ["--debug", "T"]):
            for section, field, literal, evidence, required in (
                ("warehouse", "default_stock", "-" + token, "-" + token, "integer >= 1"),
                ("warehouse", "orders_per_request", '{"nested":[' + token + "]}", token, "integer >= 1"),
                ("warehouse", "pallet_capacity_fascia", '"' + token + '"', "'" + token + "'", "integer >= 1"),
                ("identifiers", "first_order_number", "true", "True", "integer >= 1"),
                ("identifiers", "request_minimum_digits", "1.5", "1.5", "integer >= 1"),
                ("warehouse", "replenishment_threshold", token, token, "0 <= replenishment_threshold < default_stock"),
            ):
                document = copy.deepcopy(self.document)
                document[section][field] = "INVALID_VALUE"
                self.path.write_text(json.dumps(document).replace('"INVALID_VALUE"', literal), encoding="utf-8")
                with self.subTest(field=field, options=options), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
                    result = warehouse(["--config", str(self.path), "--orders", "Unvalidated.TXT", *options])
                self.assertEqual(result, 1)
                for text in (str(self.path), section + "." + field, evidence, required, "reason=configuration_failed"):
                    self.assertIn(text, err.getvalue())
                self.assertNotIn("console_logging_failed", err.getvalue())
                self.assertEqual(sys.get_int_max_str_digits(), self.limit)

    def test_csv_integer_lexical_forms_are_preserved_without_size_limit(self) -> None:
        for token, expected in ((" +" + "9" * 5000 + " ", 10 ** 5000 - 1),
                                ("_".join("９" * 5000), 10 ** 5000 - 1), ("-" + "0" * 5000, 0)):
            (self.path.parent / "Stock.CSV").write_text(
                "zone,aisle,rack,level,stock\nNorth,0,0,0," + token + "\n", encoding="utf-8")
            with self.subTest(token_prefix=token[:10]):
                config = ConfigLoader().load_config(self.path)
                self.assertTrue(next(iter(config.initial_stock.values())) == expected)
        for token in ("-" + "9" * 5000, "9" * 5000 + "__1", "\x1c1", "1.5", "True"):
            (self.path.parent / "Stock.CSV").write_text(
                "zone,aisle,rack,level,stock\nNorth,0,0,0," + token + "\n", encoding="utf-8")
            with self.subTest(token_prefix=token[:10]), self.assertRaises(ValueError) as caught:
                ConfigLoader().load_config(self.path)
            for context in ("Stock.CSV", "line=2", "field=stock", repr(token), "nonnegative integer"):
                self.assertIn(context, str(caught.exception))

    def test_large_location_rejections_and_ignored_mapping_values_are_formattable(self) -> None:
        token, expected = self.write_large_values(5000)
        document_text = self.path.read_text(encoding="utf-8")
        ignored = '"output_root":{"nested":[' + token + ']},"translation":'
        document_text = document_text.replace('"translation":', ignored, 1)
        self.path.write_text(document_text, encoding="utf-8")
        for options in ([], ["--debug", "F"], ["--debug", "T"]):
            with self.subTest(options=options), redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                _configure_console_logging(logging.DEBUG if "T" in options else logging.INFO)
                config = ConfigLoader().load_config(self.path)
                RoutePlanner(config.location_map)
                with self.assertRaises(ValueError) as caught:
                    Location("North", -expected, 0, 0)
            self.assertIn("value={'nested': [" + token + "]}", err.getvalue())
            self.assertIn("-" + token, str(caught.exception))
            if "T" in options:
                self.assertNotIn("function=route_planner.", out.getvalue())
                self.assertNotIn("function=location.", out.getvalue())
                self.assertIn("function=config_loader._read_csv action=entry", out.getvalue())
        stock_path = self.path.parent / "Stock.CSV"
        stock_path.write_text("zone,aisle,rack,level,stock\nNorth," + token + ",0,0,1\n", encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            ConfigLoader().load_config(self.path)
        for context in ("Stock.CSV", "line=2", "unknown initial-stock location", "aisle=" + token):
            self.assertIn(context, str(caught.exception))


    def test_retained_file_entries_keep_transport_error_identity_with_large_inputs(self) -> None:
        self.write_large_values(5000)
        for phase in ("config_loader.load_config", "config_loader._read_json", "config_loader._read_csv"):
            for failure in (ValueError("file diagnostic failed"), OSError("file diagnostic failed"),
                            json.JSONDecodeError("syntax-shaped diagnostic failure", "{}", 0)):
                with self.subTest(phase=phase, error_type=type(failure)):
                    captured, stderr = io.StringIO(), io.StringIO()
                    stream = Mock(wraps=captured)
                    def write(text: str) -> int:
                        if f"function={phase} action=entry " in text:
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
                    self.assertIn("console_logging_failed", stderr.getvalue())
                    self.assertNotIn("configuration_failed", stderr.getvalue())

    def test_enabled_diagnostic_context_keeps_full_large_numbers_without_changing_conversion_limit(self) -> None:
        token = "9" * 5000
        number = 10 ** 5000 - 1
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            _configure_console_logging(logging.DEBUG)
            log = ActivityLog(self.root, out)
            log.feedback("DEBUG", "inventory.decrement_stock", "action=rejected",
                         {"stock_before": number, "stock_after": -number})
            log.feedback("ERROR", "inventory.decrement_stock", "action=aborted",
                         {"stock_before": number, "stock_after": -number})
        for output in (out.getvalue(), err.getvalue()):
            self.assertIn("stock_before=" + token, output)
            self.assertIn("stock_after=-" + token, output)
        self.assertNotIn("=n/a", out.getvalue())
        self.assertIn("request_id=n/a", err.getvalue())
        self.assertEqual(sys.get_int_max_str_digits(), self.limit)

    def test_quoted_numeric_spellings_are_unchanged_and_suppressed_debug_is_lazy(self) -> None:
        spellings = ["9" * 5000, "+000_001", "NaN", "Infinity", "-Infinity"]
        self.document["extra"] = {"quoted": spellings}
        self.path.write_text(json.dumps(self.document), encoding="utf-8")
        self.assertEqual(_read_json(self.path, str(self.path))["extra"]["quoted"], spellings)
        self.write_large_values(5000)
        for options in ([], ["--debug", "F"]):
            code = textwrap.dedent(f"""
                import io, sys
                from contextlib import redirect_stdout, redirect_stderr
                from unittest.mock import patch
                from warehouse.file_io.activity_log import _diagnostic_text
                from warehouse.launcher import warehouse
                def render(value, **kwargs):
                    if type(value) is int or isinstance(value, (dict, list, tuple)):
                        raise AssertionError('suppressed numeric DEBUG evidence was rendered')
                    return _diagnostic_text(value, **kwargs)
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    with patch('warehouse.configuration.config_loader._diagnostic_text', side_effect=render):
                        assert warehouse(['--config', sys.argv[1], '--orders', sys.argv[2], *{options!r}]) == 1
                assert out.getvalue() == ''
                assert 'reason=pending_simulation' in err.getvalue()
            """)
            with self.subTest(options=options):
                result = subprocess.run([sys.executable, '-c', code, str(self.path), str(self.orders)],
                                        cwd=self.root, env={**os.environ, 'PYTHONPATH': str(PROJECT_ROOT)},
                                        capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_warning_replacements_match_real_launch_directory_and_os_timezone(self) -> None:
        self.document["files"]["output_root"] = "Ignored Output"
        self.document["identifiers"]["request_timestamp_timezone"] = "Ignored/Zone"
        self.path.write_text(json.dumps(self.document), encoding="utf-8")
        launch = self.root / "Other Launch"
        launch.mkdir()
        for zone in (None, "UTC0", "EST5EDT", "NPT-5:45"):
            environment = dict(os.environ)
            if zone is not None:
                environment["TZ"] = zone
            expected = subprocess.run(
                [sys.executable, "-c", "from datetime import datetime; import json; d=datetime.now().astimezone(); print(json.dumps([d.tzname(),d.strftime('%z')]))"],
                env=environment, capture_output=True, text=True, check=True,
            )
            timezone, offset = json.loads(expected.stdout)
            for directory in (self.root, launch):
                for supplied in (os.path.relpath(self.path, directory), str(self.path)):
                    for options in ([], ["--debug", "F"], ["--debug", "T"]):
                        with self.subTest(zone=zone, directory=directory, supplied=supplied, options=options):
                            before = set(self.root.rglob('*'))
                            result = subprocess.run(
                                [sys.executable, str(PROJECT_ROOT / "warehouse"), "--config", supplied,
                                 "--orders", str(self.orders), *options], cwd=directory,
                                env=environment, capture_output=True, text=True, check=False,
                            )
                            self.assertIn("reason=pending_simulation", result.stderr)
                            self.assertEqual(result.returncode, 1)
                            warnings = [line for line in result.stderr.splitlines() if line.startswith("[WARNING]")]
                            legacy_warnings = [line for line in warnings if 'function=config_loader.' in line]
                            self.assertEqual(len(legacy_warnings), 2)
                            self.assertTrue(all('action=collision' in line for line in warnings if line not in legacy_warnings))
                            output_warning = next(line for line in warnings if "files.output_root" in line)
                            zone_warning = next(line for line in warnings if "identifiers.request_timestamp_timezone" in line)
                            self.assertIn("output_parent=" + str(directory), output_warning)
                            self.assertIn("local_timezone=" + timezone, zone_warning)
                            self.assertIn("utc_offset=" + offset, zone_warning)
                            instant = zone_warning.split("local_instant=", 1)[1].split()[0]
                            parsed = datetime.fromisoformat(instant)
                            self.assertIsNotNone(parsed.utcoffset())
                            self.assertEqual(parsed.strftime("%z"), offset)
                            self.assertIn("Ignored Output", output_warning)
                            self.assertIn("Ignored/Zone", zone_warning)
                            self.assertNotIn("[WARNING]", result.stdout)
                            self.assertNotIn("[INFO]", result.stdout + result.stderr)
                            self.assertEqual(bool(result.stdout), "T" in options)
                            after = set(self.root.rglob('*'))
                            added = after - before
                            self.assertEqual(len(added), 1)
                            selected = added.pop()
                            self.assertEqual(selected.parent, directory)
                            self.assertEqual(list(selected.iterdir()), [])
                            self.assertIn(f'run_directory={selected}', result.stderr)
                            self.assertEqual(after, before | {selected})

    def test_each_warning_transport_failure_propagates_original_error(self) -> None:
        for field in ("output_root", "request_timestamp_timezone"):
            document = copy.deepcopy(self.document)
            document["files" if field == "output_root" else "identifiers"][field] = "Ignored"
            self.path.write_text(json.dumps(document), encoding="utf-8")
            for options in ([], ["--debug", "F"], ["--debug", "T"]):
                for operation in ("write", "flush", "short", "closed"):
                    with self.subTest(field=field, options=options, operation=operation):
                        captured = io.StringIO()
                        stream = Mock(wraps=captured)
                        failure = OSError("warning " + operation)
                        failed = False
                        def write(text: str) -> int:
                            nonlocal failed
                            if text.startswith("[WARNING]"):
                                failed = True
                                if operation == "write":
                                    raise failure
                                if operation == "short":
                                    return 1
                                if operation == "closed":
                                    captured.close()
                            return captured.write(text)
                        def flush() -> None:
                            if failed and operation == "flush":
                                raise failure
                            captured.flush()
                        stream.write.side_effect = write
                        stream.flush.side_effect = flush
                        with patch("sys.stderr", stream), redirect_stdout(io.StringIO()), patch.object(logging, "raiseExceptions", False):
                            _configure_console_logging(logging.DEBUG if "T" in options else logging.INFO)
                            with self.assertRaises(ValueError if operation == "closed" else OSError) as caught:
                                ConfigLoader().load_config(self.path)
                            if operation in ("write", "flush"):
                                self.assertIs(caught.exception, failure)
                            self.assertTrue(caught.exception.warehouse_logging_failure)
                            self.assertIsNone(caught.exception.__cause__)
                            self.assertEqual(warehouse(["--config", str(self.path), "--orders", "Unvalidated.TXT", *options]), 1)
                        if not captured.closed:
                            self.assertIn("console_logging_failed", captured.getvalue())
                            self.assertNotIn("configuration_failed", captured.getvalue())
