"""Verify console diagnostics and exclusive local-time run-folder creation.

Future cases verify partial and empty trucks,
complete traversal coverage, exact trace grammar and staged publication.
Injected console, write, flush, publish and cleanup failures must remain visible.

Planned debug cases compare INFO lines, after removing the [INFO] prefix and
following space, exactly with log.txt in T, F and omitted modes. Only
ActivityLog.record_command may emit INFO; feedback must reject INFO before
output and accept DEBUG/WARNING/ERROR. Verify T exposes all function and
lifecycle details, F/omission suppresses DEBUG, stdout/stderr routing has no
duplicates, and mirror flush precedes trace append in both modes. Empty input
has no INFO lines; generated file formats and business results stay unchanged.
"""

from __future__ import annotations

import io
import inspect
import logging
import os
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from typing import get_type_hints

from UnitTests import assert_callable_traces, isolated_console_logging
from warehouse.domain.product_catalog import ProductCatalog
from warehouse.domain.request_status import RequestStatus
from warehouse.domain.worker_type import WorkerType
from warehouse.file_io.activity_log import ActivityLog, _context_fields, _format_context
from warehouse.file_io.output_writer import OutputWriter
from warehouse.launcher import _configure_console_logging, warehouse


class RunDirectoryTests(unittest.TestCase):
    """Exercise naming, exclusive creation and preservation through real paths."""

    def setUp(self) -> None:
        self.enterContext(isolated_console_logging())
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.stdout = self.enterContext(redirect_stdout(io.StringIO()))
        self.stderr = self.enterContext(redirect_stderr(io.StringIO()))
        _configure_console_logging(logging.DEBUG)
        self.writer = OutputWriter()
        self.started_at = datetime(2026, 7, 12, 17, 42, tzinfo=timezone.utc)
        self.name = self.started_at.astimezone().strftime('warehouse_%Y%m%d%H%M')

    def test_unused_name_is_absolute_local_and_activity_log_retains_it(self) -> None:
        original = Path.cwd()
        for name in ('Mixed Case Launch', 'Other Launch'):
            launch = self.root / name
            launch.mkdir()
            selected = self.writer.create_output_directory(launch, self.started_at)
            self.assertEqual(selected, launch / self.name)
            self.assertTrue(selected.is_absolute() and selected.is_dir())
            self.assertEqual(list(selected.iterdir()), [])
            self.assertIs(ActivityLog(selected, self.stdout).run_directory, selected)
        self.assertEqual(Path.cwd(), original)
        lines = self.stdout.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(line.startswith('[DEBUG] function=output_writer.create_output_directory action=created path=') for line in lines))
        self.assertEqual(self.stderr.getvalue(), '')

    def test_folder_surface_preserves_the_exact_public_contract(self) -> None:
        method = OutputWriter.create_output_directory
        self.assertEqual(tuple(inspect.signature(method).parameters), ('self', 'working_directory', 'started_at'))
        self.assertEqual(get_type_hints(method), {'working_directory': Path, 'started_at': datetime, 'return': Path})

    def test_collisions_include_files_and_preserve_every_previous_byte(self) -> None:
        base = self.root / self.name
        candidates = [base, *(self.root / f'{self.name}_{i:02d}' for i in range(1, 4))]
        for index, candidate in enumerate(candidates):
            if index == 1:
                candidate.write_bytes(b'occupied file')
            else:
                candidate.mkdir()
                (candidate / 'Sentinel.TXT').write_bytes(f'prior {index}'.encode())
        before = {p: p.read_bytes() if p.is_file() else None for p in self.root.rglob('*')}
        real_mkdir = Path.mkdir
        attempts = []
        def mkdir(path, *args, **kwargs):
            attempts.append((path, kwargs))
            return real_mkdir(path, *args, **kwargs)
        with patch.object(Path, 'mkdir', mkdir), patch.object(Path, 'exists', side_effect=AssertionError('existence decision')):
            selected = self.writer.create_output_directory(self.root, self.started_at)
        self.assertEqual(selected, self.root / f'{self.name}_04')
        self.assertEqual(attempts, [(p, {'exist_ok': False}) for p in (*candidates, selected)])
        for path, contents in before.items():
            self.assertEqual(path.read_bytes() if path.is_file() else None, contents)
        warnings = self.stderr.getvalue().splitlines()
        self.assertEqual(warnings, [
            f'[WARNING] function=output_writer.create_output_directory action=collision next_path={next_path} '
            + _format_context(_context_fields({'path': path}))
            for path, next_path in zip(candidates, (*candidates[1:], selected))
        ])
        self.assertEqual(self.stdout.getvalue(), f'[DEBUG] function=output_writer.create_output_directory action=created path={selected}\n')

    def test_naive_time_is_rejected_before_filesystem_mutation(self) -> None:
        with patch('os.mkdir') as mkdir:
            with self.assertRaisesRegex(ValueError, 'aware'):
                self.writer.create_output_directory(self.root, datetime(2026, 1, 1))
        mkdir.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_aware_nonlocal_offset_converts_the_instant(self) -> None:
        supplied = self.started_at.astimezone(timezone(timedelta(hours=9, minutes=30)))
        selected = self.writer.create_output_directory(self.root, supplied)
        self.assertEqual(selected.name, self.name)

    def test_noncollision_os_failures_propagate_without_retry(self) -> None:
        sentinel = self.root / 'Keep.TXT'
        sentinel.write_bytes(b'preserved')
        for error in (PermissionError(13, 'denied'), FileNotFoundError(2, 'missing parent'), OSError(5, 'device failed')):
            with self.subTest(error=error), patch('os.mkdir', side_effect=error) as mkdir:
                with self.assertRaises(type(error)) as caught:
                    self.writer.create_output_directory(self.root, self.started_at)
                self.assertIs(caught.exception, error)
                self.assertEqual(error.warehouse_output_path, self.root / self.name)
                self.assertEqual(mkdir.call_count, 1)
        self.assertEqual(sentinel.read_bytes(), b'preserved')
        self.assertEqual(self.stderr.getvalue(), '')

    def test_real_unavailable_launch_directory_is_not_created(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.writer.create_output_directory(self.root / 'Missing Parent', self.started_at)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_permission_failure_after_a_real_collision_stops_at_that_candidate(self) -> None:
        base = self.root / self.name
        base.write_bytes(b'prior file')
        next_path = self.root / f'{self.name}_01'
        real_mkdir = os.mkdir
        failure = PermissionError(13, 'access denied', str(next_path))
        def mkdir(path, *args, **kwargs):
            if Path(path) == next_path:
                raise failure
            return real_mkdir(path, *args, **kwargs)
        with patch('os.mkdir', side_effect=mkdir) as boundary:
            with self.assertRaises(PermissionError) as caught:
                self.writer.create_output_directory(self.root, self.started_at)
        self.assertIs(caught.exception, failure)
        self.assertEqual([call.args[0] for call in boundary.call_args_list], [base, next_path])
        self.assertEqual(base.read_bytes(), b'prior file')
        self.assertEqual(len(self.stderr.getvalue().splitlines()), 1)
        self.assertEqual(self.stdout.getvalue(), '')

    def test_diagnostic_failures_preserve_identity_and_confirmed_filesystem_effects(self) -> None:
        from UnitTests.test_order_logging import FailingDiagnosticStream
        for action in ('collision', 'created'):
            for operation in ('write', 'flush', 'short', 'closed', 'collision_shaped'):
                with self.subTest(action=action, operation=operation):
                    launch = self.root / f'{action} {operation}'
                    launch.mkdir()
                    base = launch / self.name
                    base.write_bytes(b'preserved')
                    target = f'function=output_writer.create_output_directory action={action}'
                    stream = FailingDiagnosticStream(target, operation)
                    if operation == 'collision_shaped':
                        stream.operation = 'write'
                        stream.failure = FileExistsError(17, 'diagnostic collision')
                    stdout, stderr = (stream, io.StringIO()) if action == 'created' else (io.StringIO(), stream)
                    with (redirect_stdout(stdout), redirect_stderr(stderr),
                          patch.object(logging, 'raiseExceptions', False)):
                        _configure_console_logging(logging.DEBUG)
                        with patch('os.mkdir', wraps=os.mkdir) as mkdir:
                            with self.assertRaises((OSError, ValueError)) as caught:
                                self.writer.create_output_directory(launch, self.started_at)
                    error = caught.exception
                    self.assertTrue(stream.hit and error.warehouse_logging_failure)
                    if operation in ('write', 'flush', 'collision_shaped'):
                        self.assertIs(error, stream.failure)
                    self.assertIsNone(error.__cause__)
                    self.assertEqual(mkdir.call_count, 2 if action == 'created' else 1)
                    self.assertEqual(base.read_bytes(), b'preserved')
                    selected = launch / f'{self.name}_01'
                    if action == 'created':
                        self.assertEqual(error.warehouse_run_directory, selected)
                        self.assertEqual(error.warehouse_output_path, selected)
                        self.assertTrue(selected.is_dir())
                        self.assertEqual(list(selected.iterdir()), [])
                    else:
                        self.assertEqual(error.warehouse_output_path, base)
                        self.assertFalse(hasattr(error, 'warehouse_run_directory'))
                        self.assertFalse(selected.exists())


class ConsoleLoggingTests(unittest.TestCase):
    """Exercise shared console routing without any run-folder or trace effects."""

    def setUp(self) -> None:
        self.logger = self.enterContext(isolated_console_logging())
        self.stdout = self.enterContext(redirect_stdout(io.StringIO()))
        self.stderr = self.enterContext(redirect_stderr(io.StringIO()))

    def test_debug_omits_unavailable_context_without_losing_available_evidence(self) -> None:
        _configure_console_logging(logging.DEBUG)
        log = ActivityLog(Path("unused-run"), self.stdout)
        self.stdout.seek(0)
        self.stdout.truncate()
        context = {"request_id": None, "truck_id": "n/a", "row_count": 0,
                   "order_ids": ("O0001", "O0002"), "reason": None}
        original = context.copy()
        log.feedback("DEBUG", "order_registry.accept_order", "action=entry", context)
        self.assertEqual(self.stdout.getvalue(),
                         "[DEBUG] function=order_registry.accept_order action=entry "
                         "run_directory=unused-run order_ids=('O0001', 'O0002') row_count=0\n")
        self.assertEqual(context, original)
        self.assertEqual(self.stderr.getvalue(), "")
        log.feedback("WARNING", "order_registry.accept_order", "action=rejected", context)
        self.assertIn("request_id=n/a", self.stderr.getvalue())
        self.assertIn("truck_id=n/a", self.stderr.getvalue())
        self.assertIn("row_count=0", self.stderr.getvalue())

    def test_function_traces_have_entries_without_return_or_exit_records(self) -> None:
        _configure_console_logging(logging.DEBUG)
        catalog = ProductCatalog({("Model", "Blue"): ("001", "002")})
        self.assertEqual(catalog.resolve_skus("Model", "Blue"), ("001", "002"))
        log = ActivityLog(Path("unused-run"), self.stdout)
        log.mark_incomplete("blocked", {})
        output = self.stdout.getvalue()
        self.assertEqual(output, "")
        warehouse(["--config", "missing-config.json", "--orders", "orders.txt", "--debug", "T"])
        output = self.stdout.getvalue()
        self.assertIn("function=launcher.warehouse action=entry", output)
        self.assertNotIn("action=returned", output)
        self.assertNotIn("action=exit", output)
        self.assertIn("action=aborted result=INCOMPLETE", self.stderr.getvalue())

    def test_diagnostic_context_formatting_failure_preserves_original_error(self) -> None:
        failure = ValueError("diagnostic evidence formatting failed")
        class UnformattableEvidence:
            def __str__(self) -> str:
                raise failure
        _configure_console_logging(logging.DEBUG)
        log = ActivityLog(Path("unused-run"), self.stdout)
        with patch.object(logging, "raiseExceptions", False):
            with self.assertRaises(ValueError) as caught:
                log.feedback("DEBUG", "inventory.decrement_stock", "action=rejected",
                             {"stock_before": UnformattableEvidence()})
        self.assertIs(caught.exception, failure)
        self.assertTrue(failure.warehouse_logging_failure)
        self.assertEqual(self.stdout.getvalue(), "")

    def test_thresholds_and_routing_in_all_cli_modes(self) -> None:
        for options, threshold in (
            ([], logging.INFO), (["--debug", "F"], logging.INFO),
            (["--debug", "T"], logging.DEBUG),
        ):
            with self.subTest(options=options):
                self.stdout.seek(0)
                self.stdout.truncate()
                self.stderr.seek(0)
                self.stderr.truncate()
                result = warehouse(
                    ["--config", "Config.JSON", "--orders", "Orders.TXT", *options]
                )
                self.assertEqual(result, 1)
                self.assertEqual(self.logger.level, threshold)
                self.assertEqual(
                    [handler.level for handler in self.logger.handlers], [threshold] * 2
                )
                log = ActivityLog(Path("unused-run"), self.stdout)
                log.feedback("DEBUG", "warehouse.test", "debug evidence", {})
                log.feedback("WARNING", "warehouse.test", "recoverable problem", {})
                log.feedback("ERROR", "warehouse.test", "fatal problem", {})
                stdout, stderr = self.stdout.getvalue(), self.stderr.getvalue()
                self.assertEqual("debug evidence" in stdout, threshold == logging.DEBUG)
                self.assertTrue(all(line.startswith("[DEBUG] ") for line in stdout.splitlines()))
                self.assertNotIn("[DEBUG]", stderr)
                self.assertNotIn("[INFO]", stdout + stderr)
                self.assertEqual(stderr.count("recoverable problem"), 1)
                self.assertEqual(stderr.count("fatal problem"), 1)
                self.assertEqual(len(stderr.splitlines()), 3)

    def test_shared_module_loggers_do_not_duplicate_to_root(self) -> None:
        root_console = io.StringIO()
        root_handler = logging.StreamHandler(root_console)
        root = logging.getLogger()
        root.addHandler(root_handler)
        self.addCleanup(root.removeHandler, root_handler)
        self.addCleanup(root_handler.close)
        _configure_console_logging(logging.DEBUG)
        for name in ("warehouse", "warehouse.workflow.inventory", "warehouse.configuration"):
            module_logger = logging.getLogger(name)
            module_logger.debug("%s debug", name)
            module_logger.warning("%s warning", name)
            module_logger.error("%s error", name)
        for name in ("warehouse", "warehouse.workflow.inventory", "warehouse.configuration"):
            self.assertEqual(self.stdout.getvalue().splitlines().count(f"[DEBUG] {name} debug"), 1)
        self.assertEqual(len(self.stderr.getvalue().splitlines()), 6)
        self.assertEqual(root_console.getvalue(), "")
        self.assertFalse(self.logger.propagate)

    def test_reconfiguration_replaces_handlers_streams_and_thresholds(self) -> None:
        _configure_console_logging(logging.INFO)
        self.logger.warning("first console")
        replacement_stdout, replacement_stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(replacement_stdout), redirect_stderr(replacement_stderr):
            for level in (logging.DEBUG, logging.INFO, logging.DEBUG):
                _configure_console_logging(level)
                self.assertEqual(len(self.logger.handlers), 2)
            self.logger.debug("replacement debug")
            self.logger.warning("replacement warning")
        self.assertEqual(self.stdout.getvalue(), "")
        for output, prefix in (
            (self.stderr.getvalue(), "[WARNING] first console "),
            (replacement_stdout.getvalue(), "[DEBUG] replacement debug"),
            (replacement_stderr.getvalue(), "[WARNING] replacement warning "),
        ):
            matching = [line for line in output.splitlines() if line.startswith(prefix)]
            self.assertEqual(len(matching), 1, output)
            if prefix.startswith("[DEBUG]"):
                self.assertNotIn("n/a", output)
            else:
                self.assertIn("run_directory=n/a", output)

    def test_constructor_retains_dependencies_without_filesystem_effects(self) -> None:
        _configure_console_logging(logging.DEBUG)
        run_directory = Path("unused-run")
        with (
            patch("builtins.open") as open_file,
            patch("io.open") as path_open,
            patch("os.mkdir") as mkdir,
        ):
            log = ActivityLog(run_directory, self.stdout)
        self.assertIs(log.run_directory, run_directory)
        self.assertIs(log.console, self.stdout)
        for boundary in (open_file, path_open, mkdir):
            boundary.assert_not_called()
        self.assertEqual(self.stdout.getvalue(), "")
        self.assertEqual(self.stderr.getvalue(), "")


    def test_launcher_diagnostics_include_available_debug_and_complete_error_context(self) -> None:
        warehouse(["--config", "Config.JSON", "--orders", "Orders.TXT", "--debug", "T"])
        entries = self.stdout.getvalue().splitlines()
        self.assertEqual(len(entries), 3)
        for line in entries:
            self.assertNotIn("=n/a", line)
            self.assertRegex(line, r"^\[DEBUG\] function=[a-z_]+\.[a-z_]+ action=entry ")
            self.assertIn("Config.JSON", line)
        self.assertIn("orders_supplied=Orders.TXT", entries[0])
        for line in entries[1:]:
            self.assertIn(f"path={Path('Config.JSON').resolve()}", line)
        error = self.stderr.getvalue()
        self.assertEqual(len(error.splitlines()), 1)
        for field in ("request_id", "order_ids", "truck_id", "worker", "stock_after"):
            self.assertIn(f"{field}=n/a", error)
        self.assertIn("config supplied='Config.JSON'", error)
        self.assertIn("orders supplied='Orders.TXT'", error)

    def test_logging_setup_does_not_trace_its_own_transport(self) -> None:
        _configure_console_logging(logging.DEBUG)
        self.assertEqual(self.stdout.getvalue(), "")
        self.assertEqual(self.stderr.getvalue(), "")
        self.assertEqual(len(self.logger.handlers), 2)

    def test_mark_incomplete_reports_one_error_without_duplicate_debug_traces(self) -> None:
        _configure_console_logging(logging.DEBUG)
        log = ActivityLog(Path("unused-run"), self.stdout)
        log.mark_incomplete("blocked", {"order_ids": ("O0001", "O0002")})
        self.assertEqual(self.stdout.getvalue(), "")
        lines = self.stderr.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("order_ids=('O0001', 'O0002')", lines[0])
        self.assertIn("reason='blocked'", lines[0])

    def test_feedback_rejects_info_and_unknown_levels_before_any_output(self) -> None:
        _configure_console_logging(logging.INFO)
        log = ActivityLog(Path("unused-run"), self.stdout)
        evidence = Mock()
        evidence.__str__ = Mock(side_effect=AssertionError("must not format rejected evidence"))
        with patch.object(self.logger, "log") as emit:
            for level in ("INFO", "debug", "WARN", "CRITICAL", "", logging.INFO):
                with self.subTest(level=level), self.assertRaises(ValueError):
                    log.feedback(level, "warehouse.test", "rejected", {"evidence": evidence})
            emit.assert_not_called()
        evidence.__str__.assert_not_called()
        self.assertEqual(self.stdout.getvalue(), "")
        self.assertEqual(self.stderr.getvalue(), "")

    def test_feedback_retains_all_context_and_marks_unavailable_fields(self) -> None:
        _configure_console_logging(logging.INFO)
        log = ActivityLog(Path("unused-run"), self.stdout)
        context = {
            "request_id": "R202610311930-001", "request_sequence": 1,
            "order_ids": ("O0001", "O0002", "O0003", "O0004"),
            "truck_id": "T001", "worker_type": "picker", "worker": "Alice",
            "source": Path("Input Files/Orders.TXT"), "line_number": 8,
            "path": Path("Input Files/Stock.CSV"), "row_count": 96,
            "sku_evidence": ("1", "2", "1", "2"),
            "status_before": "picking", "status_after": "ready_for_sequencing",
            "location": ("A", 0, 0, 0), "stock_before": 6, "stock_after": 30,
        }
        original_context = context.copy()
        log.feedback(
            "WARNING", "picking_workflow.confirm_pick",
            "action=rejected", context,
        )
        diagnostic = self.stderr.getvalue()
        self.assertTrue(diagnostic.startswith("[WARNING] function=picking_workflow.confirm_pick "))
        self.assertIn("action=rejected", diagnostic)
        for field, value in context.items():
            self.assertIn(f"{field}={value}", diagnostic)
        self.assertEqual(context, original_context)
        self.assertEqual(len(diagnostic.splitlines()), 1)
        log.feedback("ERROR", "warehouse.test", "missing context", {"request_id": None})
        for field in (
            "request_id", "request_sequence", "order_ids", "truck_id", "worker",
            "source", "sku_evidence", "stock_before",
        ):
            self.assertIn(f"{field}=n/a", self.stderr.getvalue().splitlines()[-1])

    def test_enum_diagnostics_use_canonical_strings_in_all_cli_modes(self) -> None:
        for options in ([], ["--debug", "F"], ["--debug", "T"]):
            with self.subTest(options=options):
                warehouse(["--config", "Config.JSON", "--orders", "Orders.TXT", *options])
                log = ActivityLog(Path("unused-run"), self.stdout)
                self.stdout.seek(0)
                self.stdout.truncate()
                self.stderr.seek(0)
                self.stderr.truncate()
                for enum, field in ((WorkerType, "worker_type"), (RequestStatus, "status_after")):
                    for member in enum:
                        context = {field: member}
                        log.feedback("DEBUG", "warehouse.test", "enum evidence", context)
                        log.feedback("WARNING", "warehouse.test", "enum evidence", context)
                debug_lines = self.stdout.getvalue().splitlines()
                warnings = self.stderr.getvalue().splitlines()
                members = (*WorkerType, *RequestStatus)
                self.assertEqual(len(debug_lines), len(members) if "T" in options else 0)
                self.assertEqual(len(warnings), len(members))
                for lines, prefix in ((debug_lines, "[DEBUG] "), (warnings, "[WARNING] ")):
                    for line, member in zip(lines, members):
                        field = "worker_type" if isinstance(member, WorkerType) else "status_after"
                        self.assertTrue(line.startswith(prefix))
                        self.assertRegex(line, rf"{field}={member.value}(?: |$)")
                        self.assertNotIn("WorkerType.", line)
                        self.assertNotIn("RequestStatus.", line)

    def test_catalog_traces_context_once_in_all_cli_modes(self) -> None:
        for options in ([], ["--debug", "F"], ["--debug", "T"]):
            with self.subTest(options=options):
                warehouse(["--config", "Config.JSON", "--orders", "Orders.TXT", *options])
                handlers = self.logger.handlers[:]
                self.stdout.seek(0)
                self.stdout.truncate()
                self.stderr.seek(0)
                self.stderr.truncate()
                catalog = ProductCatalog({(" Straße ", " Weiß "): ("001", "020")})
                resolved = catalog.resolve_skus(" STRASSE ", " WEISS ")
                with self.assertRaises(ValueError):
                    catalog.resolve_skus("Unknown", "Blue")
                self.assertEqual(resolved, ("001", "020"))
                self.assertEqual(self.logger.handlers, handlers)
                self.assertEqual(self.stderr.getvalue(), "")
                output = self.stdout.getvalue()
                self.assertNotIn("[INFO]", output)
                if "T" not in options:
                    self.assertEqual(output, "")
                    continue
                prefix = "product_catalog."
                assert_callable_traces(self, output, ())
                for function, action, count in (
                    ("__init__", "entry", 0), ("__init__", "validated", 0),
                    ("__init__", "returned", 0), ("resolve_skus", "entry", 0),
                    ("resolve_skus", "returned", 0), ("resolve_skus", "rejected", 1),
                ):
                    matching = [line for line in output.splitlines() if line.startswith(
                        f"[DEBUG] function={prefix}{function} action={action} "
                    )]
                    self.assertEqual(len(matching), count, output)
                for evidence in (
                    "reason=unknown_product", "normalized_product=('unknown', 'blue')",
                ):
                    self.assertIn(evidence, output)
                for line in output.splitlines():
                    self.assertTrue(line.startswith("[DEBUG] "))
                    for field in ("request_id", "request_sequence", "order_ids", "truck_id",
                                  "worker_type", "worker", "source", "path", "location", "stock_after"):
                        self.assertNotIn(f"{field}=", line)
                rejected = output.splitlines()[-1]
                self.assertNotIn("sku_evidence=", rejected)

    def test_catalog_constructor_rejections_trace_supplied_and_normalized_evidence(self) -> None:
        _configure_console_logging(logging.DEBUG)
        for products, reason, evidence, function, normalized in (
            ({"AB": ("1", "2")}, "model/colour string pair", "('1', '2')",
             "_normalize_product", "n/a"),
            ({("Model", "Blue"): ["1", "2"]}, "front/back SKU tuple", "['1', '2']",
             "_validate_skus", "('model', 'blue')"),
            ({("Model", "Blue"): ("1", "0")}, "positive decimal string", "('1', '0')",
             "_validate_skus", "('model', 'blue')"),
            ({("Model", "Blue"): ("1", "2"), (" MODEL ", " BLUE "): ("3", "4")},
             "collision", "('3', '4')", "__init__", "('model', 'blue')"),
        ):
            with self.subTest(products=products):
                self.stdout.seek(0)
                self.stdout.truncate()
                with self.assertRaises(ValueError):
                    ProductCatalog(products)
                lines = self.stdout.getvalue().splitlines()
                rejected = [line for line in lines if "action=rejected " in line]
                self.assertEqual(len(rejected), 1)
                self.assertIn(f"function=product_catalog.{function} ", rejected[0])
                self.assertIn("supplied_product=", rejected[0])
                if normalized == "n/a":
                    self.assertNotIn("normalized_product=", rejected[0])
                else:
                    self.assertIn(f"normalized_product={normalized}", rejected[0])
                self.assertIn(f"sku_evidence={evidence}", rejected[0])
                self.assertIn(reason, rejected[0])
                for unfinished in {"__init__", function}:
                    self.assertFalse(any(
                        line.startswith(f"[DEBUG] function=product_catalog.{unfinished} action=returned ")
                        for line in lines
                    ))
        self.assertEqual(self.stderr.getvalue(), "")

    def test_catalog_suppressed_debug_does_not_format_evidence(self) -> None:
        class UnformattableString(str):
            def __str__(self) -> str:
                raise AssertionError("suppressed string evidence formatted")

            def __repr__(self) -> str:
                raise AssertionError("suppressed string evidence repr formatted")

        failing_stream = Mock()
        failing_stream.write.side_effect = OSError("stdout unavailable")
        with patch("sys.stdout", failing_stream):
            _configure_console_logging(logging.INFO)
            catalog = ProductCatalog({(UnformattableString("Model"), "Blue"):
                                      (UnformattableString("001"), "002")})
            self.assertEqual(catalog.resolve_skus("model", "blue"), ("001", "002"))
        failing_stream.write.assert_not_called()
        failing_stream.flush.assert_not_called()

    def test_diagnostics_use_short_labels_and_one_newline_per_stream_write(self) -> None:
        stdout, stderr = Mock(wraps=io.StringIO()), Mock(wraps=io.StringIO())
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            _configure_console_logging(logging.DEBUG)
            catalog = ProductCatalog({("Model", "Blue"): ("1", "2")})
            catalog.resolve_skus("Model", "Blue")
            with self.assertRaises(ValueError):
                catalog.resolve_skus("Unknown", "Blue")
            log = ActivityLog(Path("unused-run"), stdout)
            log.feedback("WARNING", "product_catalog.resolve_skus", "unknown product", {})
            log.mark_incomplete("blocked", {})
        for stream in (stdout, stderr):
            self.assertTrue(stream.write.call_args_list)
            for call in stream.write.call_args_list:
                text = call.args[0]
                self.assertTrue(text.endswith("\n"), text)
                self.assertEqual(text.count("\n"), 1, text)
                self.assertRegex(text, r"^\[(DEBUG|WARNING|ERROR)\] function=[a-z_]+\.[a-z_]+ ")
                self.assertNotIn("function=warehouse.", text)
        _configure_console_logging(logging.INFO)


    def test_catalog_rejection_transport_errors_keep_identity_and_do_not_mutate_source(self) -> None:
        for function, source in (
            ("_normalize_product", {"AB": ("1", "2")}),
            ("_validate_skus", {("Model", "Blue"): ("1", "0")}),
            ("__init__", {("Model", "Blue"): ("1", "2"), (" MODEL ", " BLUE "): ("3", "4")}),
        ):
            for operation in ("write", "flush", "short", "closed"):
                with self.subTest(function=function, operation=operation):
                    original = source.copy()
                    captured = io.StringIO()
                    stream = Mock(wraps=captured)
                    failure = ValueError("catalog rejection transport failed")
                    target = f"function=product_catalog.{function} action=rejected "
                    triggered = False
                    def write(text: str) -> int:
                        nonlocal triggered
                        if target in text:
                            triggered = True
                            if operation == "write":
                                raise failure
                            if operation == "short":
                                return 1
                            if operation == "closed":
                                closed = io.StringIO()
                                closed.close()
                                return closed.write(text)
                        return captured.write(text)
                    def flush() -> None:
                        if triggered and operation == "flush":
                            raise failure
                        captured.flush()
                    stream.write.side_effect = write
                    stream.flush.side_effect = flush
                    with patch("sys.stdout", stream), patch.object(logging, "raiseExceptions", False):
                        _configure_console_logging(logging.DEBUG)
                        with self.assertRaises(OSError if operation == "short" else ValueError) as caught:
                            ProductCatalog(source)
                    self.assertTrue(triggered)
                    self.assertTrue(caught.exception.warehouse_logging_failure)
                    if operation in ("write", "flush"):
                        self.assertIs(caught.exception, failure)
                    self.assertEqual(source, original)
                    _configure_console_logging(logging.INFO)


    def test_unknown_product_diagnostic_transport_failures_preserve_catalog_identity(self) -> None:
        products = {("Model", "Blue"): ("1", "2")}
        catalog = ProductCatalog(products)
        mapping = catalog.products
        before = dict(mapping)
        for operation in ("write", "flush", "short_write", "closed"):
            with self.subTest(operation=operation):
                captured = io.StringIO()
                stream = Mock(wraps=captured)
                with patch("sys.stdout", stream), patch.object(logging, "raiseExceptions", False):
                    _configure_console_logging(logging.DEBUG)
                    if operation == "closed":
                        captured.close()
                    elif operation == "short_write":
                        stream.write.return_value = 1
                    else:
                        getattr(stream, operation).side_effect = OSError(f"catalog {operation} failed")
                    with self.assertRaises(ValueError if operation == "closed" else OSError) as caught:
                        catalog.resolve_skus("Unknown", "Blue")
                    self.assertTrue(caught.exception.warehouse_logging_failure)
                self.assertEqual(products, {("Model", "Blue"): ("1", "2")})
                self.assertIs(catalog.products, mapping)
                self.assertEqual(dict(mapping), before)
        _configure_console_logging(logging.INFO)

    def test_mark_incomplete_reports_reason_and_available_trace_context(self) -> None:
        _configure_console_logging(logging.INFO)
        log = ActivityLog(Path("unused-run"), self.stdout)
        for context, confirmed in (
            ({}, "n/a"),
            (
                {"last_confirmed_trace_line": "Order Model Blue", "order_ids": ("O0001",)},
                "Order Model Blue",
            ),
        ):
            with self.subTest(context=context):
                log.mark_incomplete("console unavailable", context)
                line = self.stderr.getvalue().splitlines()[-1]
                self.assertIn(
                    "function=activity_log.mark_incomplete", line
                )
                self.assertIn("result=INCOMPLETE", line)
                self.assertIn("reason='console unavailable'", line)
                self.assertIn(f"last_confirmed_trace_line={confirmed}", line)
                self.assertIn("run_directory=unused-run", line)
        self.assertEqual(self.stdout.getvalue(), "")

    def test_diagnostic_write_and_flush_failures_propagate(self) -> None:
        for level, stream_name in (
            ("DEBUG", "stdout"), ("WARNING", "stderr"), ("ERROR", "stderr")
        ):
            for operation in ("write", "flush"):
                with self.subTest(level=level, operation=operation), ExitStack() as stack:
                    _configure_console_logging(logging.INFO)
                    log = ActivityLog(Path("unused-run"), self.stdout)
                    failing_stream = Mock(wraps=io.StringIO())
                    failure = OSError(f"{stream_name} {operation} failed")
                    stack.enter_context(patch(f"sys.{stream_name}", failing_stream))
                    stack.enter_context(patch.object(logging, "raiseExceptions", False))
                    _configure_console_logging(logging.DEBUG)
                    failing_stream.reset_mock()
                    getattr(failing_stream, operation).side_effect = failure
                    with self.assertRaises(OSError) as caught:
                        log.feedback(level, "warehouse.test", "diagnostic", {})
                    self.assertIs(caught.exception, failure)
                    if operation == "write":
                        failing_stream.flush.assert_not_called()

    def test_short_write_and_closed_stream_failures_propagate(self) -> None:
        _configure_console_logging(logging.INFO)
        log = ActivityLog(Path("unused-run"), self.stdout)
        stream = Mock(wraps=io.StringIO())
        with patch("sys.stdout", stream):
            _configure_console_logging(logging.DEBUG)
            stream.reset_mock()
            stream.write.return_value = 1
            with self.assertRaisesRegex(OSError, "complete diagnostic"):
                log.feedback("DEBUG", "warehouse.test", "short write", {})
        stream.flush.assert_not_called()
        closed_stream = io.StringIO()
        with patch("sys.stdout", closed_stream):
            _configure_console_logging(logging.DEBUG)
            closed_stream.close()
            with self.assertRaises(ValueError):
                log.feedback("DEBUG", "warehouse.test", "closed stream", {})

    def test_suppressed_debug_never_writes_or_formats_evidence(self) -> None:
        failing_stream = Mock()
        failing_stream.write.side_effect = OSError("stdout unavailable")
        evidence = Mock()
        evidence.__str__ = Mock(side_effect=AssertionError("suppressed evidence formatted"))
        with patch("sys.stdout", failing_stream):
            _configure_console_logging(logging.INFO)
            log = ActivityLog(Path("unused-run"), failing_stream)
            log.feedback("DEBUG", "warehouse.test", "suppressed", {"evidence": evidence})
        failing_stream.write.assert_not_called()
        failing_stream.flush.assert_not_called()
        evidence.__str__.assert_not_called()


class LauncherLoggingFailureTests(unittest.TestCase):
    """Check nonzero exits and direct reporting when console logging breaks."""

    def setUp(self) -> None:
        self.enterContext(isolated_console_logging())

    def test_logging_setup_failures_use_fallback_before_any_startup_work(self) -> None:
        for debug in ([], ["--debug", "F"], ["--debug", "T"]):
            for failure in (OSError("handler setup failed"), ValueError("handler closed")):
                with self.subTest(debug=debug, failure=failure), ExitStack() as stack:
                    stack.enter_context(patch(
                        "warehouse.launcher._configure_console_logging", side_effect=failure
                    ))
                    stdout = stack.enter_context(redirect_stdout(io.StringIO()))
                    stderr = stack.enter_context(redirect_stderr(io.StringIO()))
                    boundaries = [stack.enter_context(patch(target)) for target in (
                        "warehouse.launcher.Path.cwd", "warehouse.launcher.Path.resolve",
                        "builtins.open", "io.open", "os.mkdir",
                        "warehouse.configuration.config_loader.ConfigLoader.load_config",
                        "warehouse.file_io.order_reader.OrderReader.read_orders",
                        "warehouse.domain.warehouse_state.WarehouseState",
                    )]
                    result = warehouse([
                        "--config", "Config.JSON", "--orders", "Orders.TXT", *debug
                    ])
                    self.assertEqual(result, 1)
                    self.assertEqual(stdout.getvalue(), "")
                    fallback = stderr.getvalue()
                    self.assertIn("reason=console_logging_failed", fallback)
                    self.assertIn(str(failure), fallback)
                    self.assertIn("result=INCOMPLETE", fallback)
                    self.assertIn("last_confirmed_trace_line=n/a", fallback)
                    self.assertIn("worker_type=n/a", fallback)
                    self.assertIn("config_supplied=Config.JSON", fallback)
                    self.assertIn("orders_supplied=Orders.TXT", fallback)
                    for boundary in boundaries:
                        boundary.assert_not_called()

    def test_handler_construction_failure_uses_direct_fallback(self) -> None:
        with (
            patch("warehouse.launcher._CheckedStreamHandler", side_effect=OSError("no handler")),
            redirect_stdout(io.StringIO()) as stdout,
            redirect_stderr(io.StringIO()) as stderr,
            patch("warehouse.launcher.Path.cwd") as cwd,
        ):
            self.assertEqual(warehouse([
                "--config", "Config.JSON", "--orders", "Orders.TXT", "--debug", "T"
            ]), 1)
        cwd.assert_not_called()
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("reason=console_logging_failed error='no handler'", stderr.getvalue())
        self.assertIn("config_resolved=n/a", stderr.getvalue())
        self.assertIn("orders_resolved=n/a", stderr.getvalue())
        self.assertIn("working_directory=n/a", stderr.getvalue())

    def test_existing_handler_close_failure_uses_direct_fallback(self) -> None:
        handler = logging.StreamHandler(io.StringIO())
        logging.getLogger("warehouse").addHandler(handler)
        with (
            patch.object(handler, "close", side_effect=ValueError("handler close failed")),
            redirect_stdout(io.StringIO()) as stdout,
            redirect_stderr(io.StringIO()) as stderr,
            patch("warehouse.launcher.Path.cwd") as cwd,
        ):
            self.assertEqual(warehouse([
                "--config", "Config.JSON", "--orders", "Orders.TXT"
            ]), 1)
        cwd.assert_not_called()
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("reason=console_logging_failed", stderr.getvalue())
        self.assertIn("handler close failed", stderr.getvalue())

    def test_stdout_failure_stops_diagnostics_and_uses_direct_stderr(self) -> None:
        for operation in ("write", "flush"):
            with self.subTest(operation=operation), ExitStack() as stack:
                stdout = Mock(wraps=io.StringIO())
                getattr(stdout, operation).side_effect = OSError(f"stdout {operation} failed")
                stack.enter_context(patch("sys.stdout", stdout))
                stderr = stack.enter_context(redirect_stderr(io.StringIO()))
                boundaries = [stack.enter_context(patch(target)) for target in (
                    "builtins.open", "io.open", "os.mkdir", "os.chdir",
                    "warehouse.configuration.config_loader.ConfigLoader.load_config",
                    "warehouse.file_io.order_reader.OrderReader.read_orders",
                    "warehouse.domain.warehouse_state.WarehouseState",
                )]
                result = warehouse([
                    "--config", "Config.JSON", "--orders", "Orders.TXT", "--debug", "T"
                ])
                self.assertEqual(result, 1)
                self.assertEqual(stdout.write.call_count, 1)
                for boundary in boundaries:
                    boundary.assert_not_called()
                fallback = stderr.getvalue()
                self.assertEqual(len(fallback.splitlines()), 1)
                self.assertTrue(fallback.startswith("[ERROR] "))
                self.assertIn("reason=console_logging_failed", fallback)
                self.assertIn(f"stdout {operation} failed", fallback)
                self.assertIn("result=INCOMPLETE", fallback)
                self.assertIn("last_confirmed_trace_line=n/a", fallback)
                self.assertNotIn("state composed; simulation execution is not implemented", fallback)

    def test_transient_stderr_failure_uses_fallback_in_all_modes(self) -> None:
        for debug in ([], ["--debug", "F"], ["--debug", "T"]):
            for operation in ("write", "flush"):
                with self.subTest(debug=debug, operation=operation):
                    captured = io.StringIO()
                    stderr = Mock(wraps=captured)
                    original_operation = getattr(captured, operation)
                    failed = False

                    def fail_once(*args: object) -> object:
                        nonlocal failed
                        if not failed:
                            failed = True
                            raise OSError(f"stderr {operation} failed")
                        return original_operation(*args)

                    getattr(stderr, operation).side_effect = fail_once
                    with redirect_stdout(io.StringIO()) as stdout, patch("sys.stderr", stderr):
                        result = warehouse([
                            "--config", "Config.JSON", "--orders", "Orders.TXT", *debug
                        ])
                    self.assertEqual(result, 1)
                    self.assertIn("reason=console_logging_failed", captured.getvalue())
                    self.assertEqual(captured.getvalue().count("reason=console_logging_failed"), 1)
                    self.assertIn(f"stderr {operation} failed", captured.getvalue())
                    self.assertNotIn("action=returned exit_code=1", stdout.getvalue())

    def test_failed_fallback_still_returns_nonzero(self) -> None:
        for failure in (OSError("stderr unavailable"), ValueError("stderr closed")):
            with self.subTest(failure=failure):
                stderr = Mock()
                stderr.write.side_effect = failure
                with redirect_stdout(io.StringIO()), patch("sys.stderr", stderr):
                    result = warehouse(["--config", "Config.JSON", "--orders", "Orders.TXT"])
                self.assertEqual(result, 1)
                self.assertEqual(stderr.write.call_count, 2)


# TODO (step 21): Check exact command grammar, checked mirrors and trace I/O.
# TODO (step 22): Check manifests, final inventory and publication failures.
