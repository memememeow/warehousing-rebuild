"""Exercise retained order-preflight diagnostics through real checked handlers."""

from __future__ import annotations

import io
import logging
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from UnitTests import isolated_console_logging
from UnitTests.test_configuration import PROJECT_ROOT, write_configuration
from warehouse.file_io.order_reader import OrderReader
from warehouse.launcher import _configure_console_logging


READER_EVENTS = (
    ("valid", "function=order_reader.read_orders action=validated"),
    ("missing", "function=order_reader.read_orders action=rejected reason=read_failed"),
    ("decode", "function=order_reader.read_orders action=rejected reason=invalid_utf8"),
    ("grammar", "function=order_reader.parse_order action=rejected reason=invalid_grammar"),
)


class FailingDiagnosticStream(io.StringIO):
    """Fail only the selected diagnostic, leaving other real emissions intact."""

    def __init__(self, target: str, operation: str) -> None:
        super().__init__()
        self.target = target
        self.operation = operation
        self.hit = False
        self.flush_pending = False
        self.failure = (
            OSError("diagnostic write failed") if operation == "write"
            else ValueError("diagnostic flush failed")
        )

    def write(self, text: str) -> int:
        if self.target in text:
            self.hit = True
            self.flush_pending = True
            if self.operation == "write":
                raise self.failure
            if self.operation == "short":
                return len(text) - 1
            if self.operation == "closed":
                closed = io.StringIO()
                closed.close()
                return closed.write(text)
        return super().write(text)

    def flush(self) -> None:
        if self.flush_pending and self.operation == "flush":
            self.flush_pending = False
            raise self.failure
        super().flush()


class OrderLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(isolated_console_logging())
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.config, _ = write_configuration(self.root / "Inputs")

    def test_each_reader_event_propagates_original_transport_failures(self) -> None:
        for case, target in READER_EVENTS:
            path = self.root / f"{case}.txt"
            if case != "missing":
                path.write_bytes({"valid": b"Order Unknown Purple", "decode": b"\xff",
                                  "grammar": b"Order Model Blue\nOrder missing"}[case])
            for operation in ("write", "flush", "short", "closed"):
                with self.subTest(case=case, operation=operation):
                    stream = FailingDiagnosticStream(target, operation)
                    with (redirect_stdout(stream), redirect_stderr(io.StringIO()) as err,
                          patch.object(logging, "raiseExceptions", False)):
                        _configure_console_logging(logging.DEBUG)
                        with self.assertRaises(ValueError if operation in ("flush", "closed") else OSError) as caught:
                            OrderReader().read_orders(path)
                    self.assertTrue(stream.hit)
                    self.assertTrue(caught.exception.warehouse_logging_failure)
                    self.assertIsNone(caught.exception.__cause__)
                    if operation in ("write", "flush"):
                        self.assertIs(caught.exception, stream.failure)
                    self.assertEqual(err.getvalue(), "")

    def test_decoder_shaped_diagnostic_error_is_not_translated_to_input_failure(self) -> None:
        path = self.root / "orders.txt"
        for case, target in READER_EVENTS:
            if path.exists():
                path.unlink()
            if case != "missing":
                path.write_bytes({"valid": b"Order Unknown Purple", "decode": b"\xff",
                                  "grammar": b"Order missing"}[case])
            with self.subTest(case=case):
                stream = FailingDiagnosticStream(target, "write")
                failure = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "diagnostic decoder failed")
                stream.failure = failure
                with (redirect_stdout(stream), redirect_stderr(io.StringIO()),
                      patch.object(logging, "raiseExceptions", False)):
                    _configure_console_logging(logging.DEBUG)
                    with self.assertRaises(UnicodeDecodeError) as caught:
                        OrderReader().read_orders(path)
                self.assertIs(caught.exception, failure)
                self.assertIsNone(failure.__cause__)
                self.assertTrue(failure.warehouse_logging_failure)

    def run_launcher_failure(self, case: str, target: str, operation: str,
                             options: list[str], unavailable_stderr: bool = False) -> None:
        path = self.root / f"{case}.txt"
        if case != "missing":
            path.write_bytes({"valid": b"Order Unknown Purple", "decode": b"\xff",
                              "grammar": b"Order Model Blue\nOrder missing"}[case])
        code = textwrap.dedent(f"""
            import io, logging, sys
            from contextlib import redirect_stdout, redirect_stderr
            from pathlib import Path
            from UnitTests.test_order_logging import FailingDiagnosticStream
            from warehouse.launcher import warehouse, _numeric_initialization_state
            logging.raiseExceptions = False
            path, config = map(Path, sys.argv[1:3])
            root = path.parent
            before = {{p: p.read_bytes() if p.is_file() else None for p in root.rglob('*')}}
            target = {target!r}
            stream = FailingDiagnosticStream(target, {operation!r})
            out, err = (stream, io.StringIO()) if target.startswith('function=order_reader') else (io.StringIO(), stream)
            if {unavailable_stderr!r}:
                class Unavailable(io.StringIO):
                    def write(self, text): raise OSError('stderr unavailable')
                err = Unavailable()
            calls = []
            def profile(frame, event, arg):
                if event == 'call':
                    module, name = frame.f_globals.get('__name__'), frame.f_code.co_name
                    if (module, name) in (
                        ('warehouse.launcher', '_initialize_numeric_constants'),
                        ('warehouse.domain.warehouse_state', '__init__'),
                        ('warehouse.file_io.output_writer', 'create_output_directory'),
                        ('warehouse.workflow.warehouse_application', 'run'),
                    ): calls.append((module, name))
            sys.setprofile(profile)
            try:
                with redirect_stdout(out), redirect_stderr(err):
                    result = warehouse(['--config', str(config), '--orders', str(path), *{options!r}])
            finally: sys.setprofile(None)
            assert result == 1 and stream.hit
            after_numeric = 'reason=pending_simulation' in target
            assert calls == ([('warehouse.launcher', '_initialize_numeric_constants'),
                              ('warehouse.domain.warehouse_state', '__init__'),
                              ('warehouse.file_io.output_writer', 'create_output_directory')]
                             if after_numeric else []), calls
            state = _numeric_initialization_state()
            assert all(type(value) is int if after_numeric else value == 'unpublished' for value in state.values())
            if not {unavailable_stderr!r}:
                fallback = [line for line in err.getvalue().splitlines() if 'reason=console_logging_failed' in line]
                assert len(fallback) == 1, err.getvalue()
                for field in ('last_confirmed_trace_line=n/a', 'numeric_initialization=', str(path), str(config)):
                    assert field in fallback[0], field
            assert '[INFO]' not in out.getvalue() + err.getvalue()
            after = {{p: p.read_bytes() if p.is_file() else None for p in root.rglob('*')}}
            assert all(after[p] == value for p, value in before.items())
            added = set(after) - set(before)
            assert len(added) == (1 if after_numeric else 0), added
            if after_numeric:
                selected = added.pop()
                assert selected.parent == root and selected.is_dir() and not list(selected.iterdir())
                assert f'run_directory={{selected}}' in err.getvalue()
        """)
        result = subprocess.run([sys.executable, "-c", code, str(path), str(self.config)],
                                cwd=self.root, env={**os.environ, 'PYTHONPATH': str(PROJECT_ROOT)},
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_reader_event_failures_stop_numeric_work_and_use_direct_fallback(self) -> None:
        for case, target in READER_EVENTS:
            for operation in ("write", "flush", "short", "closed"):
                with self.subTest(case=case, operation=operation):
                    self.run_launcher_failure(case, target, operation, ["--debug", "T"])

    def test_order_error_and_pending_boundary_transport_failures_in_every_mode(self) -> None:
        for case, reason in (("grammar", "order_preflight_failed"), ("missing", "order_preflight_failed"),
                             ("decode", "order_preflight_failed"), ("valid", "pending_simulation")):
            for options in ([], ["--debug", "F"], ["--debug", "T"]):
                for operation in ("write", "flush", "short", "closed"):
                    with self.subTest(case=case, options=options, operation=operation):
                        self.run_launcher_failure(case, f"reason={reason}", operation, options)

    def test_unavailable_stderr_still_stops_at_each_reader_failure(self) -> None:
        for case, target in READER_EVENTS:
            with self.subTest(case=case):
                self.run_launcher_failure(case, target, "write", ["--debug", "T"], unavailable_stderr=True)
