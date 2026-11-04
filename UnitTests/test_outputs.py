"""Verify step 02 console diagnostics; reserve trace and publication coverage.

Future cases verify exclusive folder collisions, partial and empty trucks,
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
import logging
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from UnitTests import assert_callable_traces, isolated_console_logging
from warehouse.domain.product_catalog import ProductCatalog
from warehouse.domain.request_status import RequestStatus
from warehouse.domain.worker_type import WorkerType
from warehouse.file_io.activity_log import ActivityLog
from warehouse.launcher import _configure_console_logging, warehouse


class ConsoleLoggingTests(unittest.TestCase):
    """Exercise shared console routing without any run-folder or trace effects."""

    def setUp(self) -> None:
        self.logger = self.enterContext(isolated_console_logging())
        self.stdout = self.enterContext(redirect_stdout(io.StringIO()))
        self.stderr = self.enterContext(redirect_stderr(io.StringIO()))

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
            self.assertEqual(self.stdout.getvalue().count(f"] {name} debug "), 1)
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
            (replacement_stdout.getvalue(), "[DEBUG] replacement debug "),
            (replacement_stderr.getvalue(), "[WARNING] replacement warning "),
        ):
            matching = [line for line in output.splitlines() if line.startswith(prefix)]
            self.assertEqual(len(matching), 1, output)
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
        assert_callable_traces(self, self.stdout.getvalue(), (
            "warehouse.file_io.activity_log.ActivityLog.__init__",
        ))
        self.assertEqual(self.stderr.getvalue(), "")

    def test_launcher_diagnostics_have_complete_context(self) -> None:
        warehouse(["--config", "Config.JSON", "--orders", "Orders.TXT", "--debug", "T"])
        for line in (self.stdout.getvalue() + self.stderr.getvalue()).splitlines():
            with self.subTest(line=line):
                is_reader = "function=warehouse.configuration.config_loader." in line
                is_loader_constructor = "ConfigLoader.__init__ " in line
                for field in (
                    "run_directory", "request_id", "request_sequence", "order_ids",
                    "truck_id", "worker_type", "worker", "source", "line_number",
                    "path", "row_count", "sku_evidence", "status_before", "status_after",
                    "location", "stock_before", "stock_after",
                ):
                    if is_reader and not is_loader_constructor and field == "source":
                        if "ConfigLoader.load_config" in line:
                            self.assertIn(f"source={Path('Config.JSON').resolve()}", line)
                        else:
                            self.assertIn(f"source=config supplied={str(Path('Config.JSON').resolve())!r}", line)
                        continue
                    if is_reader and not is_loader_constructor and field == "path":
                        self.assertIn(f"path={Path('Config.JSON').resolve()}", line)
                        continue
                    self.assertIn(f"{field}=n/a", line)
                if is_loader_constructor:
                    if "action=returned" in line:
                        self.assertIn(f"output_parent={Path.cwd()}", line)
                elif "function=warehouse.launcher._configure_console_logging " not in line:
                    self.assertIn("Config.JSON", line)
                    if not is_reader:
                        self.assertIn("Orders.TXT", line)

    def test_logging_setup_helper_has_debug_entry_and_result(self) -> None:
        _configure_console_logging(logging.DEBUG)
        assert_callable_traces(self, self.stdout.getvalue(), (
            "warehouse.launcher._configure_console_logging",
        ))

    def test_mark_incomplete_helper_has_debug_entry_and_result(self) -> None:
        _configure_console_logging(logging.DEBUG)
        log = ActivityLog(Path("unused-run"), self.stdout)
        log.mark_incomplete("blocked", {"order_ids": ("O0001", "O0002")})
        assert_callable_traces(self, self.stdout.getvalue(), (
            "warehouse.file_io.activity_log.ActivityLog.mark_incomplete",
        ))
        for line in self.stdout.getvalue().splitlines():
            if "ActivityLog.mark_incomplete" in line:
                self.assertIn("order_ids=('O0001', 'O0002')", line)
                self.assertIn("reason='blocked'", line)

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
            "WARNING", "warehouse.workflow.picking_workflow.PickingWorkflow.confirm_pick",
            "action=rejected", context,
        )
        diagnostic = self.stderr.getvalue()
        self.assertTrue(diagnostic.startswith("[WARNING] function=warehouse.workflow."))
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
                        self.assertIn(f"{field}={member.value} ", line)
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
                prefix = "warehouse.domain.product_catalog.ProductCatalog."
                assert_callable_traces(self, output, (prefix + "__init__", prefix + "resolve_skus"))
                for function, action, count in (
                    ("__init__", "entry", 1), ("__init__", "validated", 1),
                    ("__init__", "returned", 1), ("resolve_skus", "entry", 2),
                    ("resolve_skus", "returned", 1), ("resolve_skus", "rejected", 1),
                ):
                    matching = [line for line in output.splitlines() if line.startswith(
                        f"[DEBUG] function={prefix}{function} action={action} "
                    )]
                    self.assertEqual(len(matching), count, output)
                for evidence in (
                    "supplied_product=(' Straße ', ' Weiß ')",
                    "supplied_product=(' STRASSE ', ' WEISS ')",
                    "normalized_product=('strasse', 'weiss')", "sku_evidence=('001', '020')",
                    "reason=unknown_product", "normalized_product=('unknown', 'blue')",
                ):
                    self.assertIn(evidence, output)
                for line in output.splitlines():
                    self.assertTrue(line.startswith("[DEBUG] "))
                    for field in ("request_id", "request_sequence", "order_ids", "truck_id",
                                  "worker_type", "worker", "source", "path", "location", "stock_after"):
                        self.assertIn(f"{field}=n/a", line)
                rejected = output.splitlines()[-1]
                self.assertIn("sku_evidence=n/a", rejected)

    def test_catalog_constructor_rejections_trace_supplied_and_normalized_evidence(self) -> None:
        _configure_console_logging(logging.DEBUG)
        for products, reason, evidence in (
            ({("Model", "Blue"): ("1", "0")}, "positive decimal string", "('1', '0')"),
            ({("Model", "Blue"): ("1", "2"), (" MODEL ", " BLUE "): ("3", "4")},
             "collision", "('3', '4')"),
        ):
            with self.subTest(products=products):
                self.stdout.seek(0)
                self.stdout.truncate()
                with self.assertRaises(ValueError):
                    ProductCatalog(products)
                lines = self.stdout.getvalue().splitlines()
                rejected = [line for line in lines if "action=rejected " in line]
                self.assertEqual(len(rejected), 1)
                self.assertIn("ProductCatalog.__init__", rejected[0])
                self.assertIn("supplied_product=", rejected[0])
                self.assertIn("normalized_product=('model', 'blue')", rejected[0])
                self.assertIn(f"sku_evidence={evidence}", rejected[0])
                self.assertIn(reason, rejected[0])
                self.assertFalse(any("action=returned " in line for line in lines))
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

    def test_catalog_diagnostic_transport_failures_propagate_without_source_mutation(self) -> None:
        products = {("Model", "Blue"): ("1", "2")}
        catalog = ProductCatalog(products)
        mapping = catalog.products
        before = dict(mapping)
        for operation in ("write", "flush", "short_write", "closed"):
            for constructing in (True, False):
                with self.subTest(operation=operation, constructing=constructing):
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
                        with self.assertRaises(ValueError if operation == "closed" else OSError):
                            if constructing:
                                ProductCatalog(products)
                            else:
                                catalog.resolve_skus("model", "blue")
                    self.assertEqual(products, {("Model", "Blue"): ("1", "2")})
                    self.assertIs(catalog.products, mapping)
                    self.assertEqual(dict(mapping), before)
        # Restore a live stream before the isolated logging context closes handlers.
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
                    "function=warehouse.file_io.activity_log.ActivityLog.mark_incomplete", line
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
                self.assertNotIn("simulation startup is not implemented", fallback)

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
