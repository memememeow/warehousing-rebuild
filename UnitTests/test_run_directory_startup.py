"""Observe real startup and inject faults only at OS/diagnostic boundaries."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from UnitTests.test_configuration import PROJECT_ROOT, write_configuration


STARTUP_PROBE = r'''
import io, logging, os, sys
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from UnitTests.test_order_logging import FailingDiagnosticStream
from warehouse.launcher import warehouse, _numeric_initialization_state
from warehouse.file_io.activity_log import ActivityLog
from warehouse.workflow.warehouse_application import WarehouseApplication
from typing import get_type_hints

config, orders, mode, case, operation = sys.argv[1:]
options = [] if mode == 'omitted' else ['--debug', mode]
launch = Path.cwd()
original_inputs = {p: p.read_bytes() for p in Path(config).parent.iterdir() if p.is_file()}
logging.raiseExceptions = False
events, observed, attempts, clocks, cwds = [], {}, [], [], []
real_mkdir = os.mkdir
failure = {'permission': PermissionError(13, 'output access denied'),
           'missing': FileNotFoundError(2, 'output parent unavailable'),
           'device': OSError(5, 'output device failed')}.get(case)
if case == 'collision':
    target = 'function=output_writer.create_output_directory action=collision'
elif case in ('selected', 'suppressed'):
    target = 'function=output_writer.create_output_directory action=created'
elif case == 'fatal':
    target = 'action=aborted result=INCOMPLETE reason=run_folder_failed'
    failure = PermissionError(13, 'output access denied')
else:
    target = 'reason=pending_simulation'

stream = FailingDiagnosticStream(target, operation)
if operation == 'collision_shaped':
    stream.operation = 'write'
    stream.failure = FileExistsError(17, 'diagnostic transport collision')
out, err = (stream, io.StringIO()) if case in ('selected', 'suppressed') else (io.StringIO(), stream)
if operation == 'unavailable':
    class Unavailable(io.StringIO):
        def write(self, text): raise OSError('stderr unavailable')
    err = Unavailable()
    if case == 'selected':
        stream.operation = 'write'
    else:
        stream = err
fixture = {}

def profile(frame, event, arg):
    module, name = frame.f_globals.get('__name__'), frame.f_code.co_name
    if event == 'c_call' and getattr(arg, '__name__', '') == 'now':
        clocks.append(arg.__self__)
    if event == 'call' and name == 'cwd' and module.startswith('pathlib'):
        cwds.append(name)
    if event == 'call' and module in ('warehouse.domain.warehouse_state', 'warehouse.workflow.warehouse_application'):
        raise AssertionError('later execution started')
    if event == 'return' and module == 'warehouse.configuration.config_loader' and name == 'load_config':
        events.append('config'); observed['config'] = arg
        assert all(v == 'unpublished' for v in _numeric_initialization_state().values())
    if event == 'return' and module == 'warehouse.file_io.order_reader' and name == 'read_orders':
        assert events == ['config']
        assert type(arg) is tuple and arg == ((2, 'Model', 'BLUE'), (4, 'Unknown', 'Purple'))
        events.append('orders'); observed['orders'] = arg
    if event == 'return' and module == 'warehouse.launcher' and name == '_initialize_numeric_constants':
        assert events == ['config', 'orders']
        assert frame.f_locals['config'] is observed['config']
        assert all(type(v) is int for v in _numeric_initialization_state().values())
        observed['numeric'] = _numeric_initialization_state()
        events.append('numeric')
    if module == 'warehouse.file_io.output_writer' and name == 'create_output_directory':
        if event == 'call':
            assert events == ['config', 'orders', 'numeric']
            events.append('folder')
            observed.update(frame.f_locals)
            start = frame.f_locals['started_at']
            assert start.utcoffset() is not None
            assert frame.f_locals['working_directory'] == launch
            base = launch / start.astimezone().strftime('warehouse_%Y%m%d%H%M')
            observed['base'] = base
            if case in ('success_collisions', 'collision'):
                for index in range(3):
                    path = base if index == 0 else base.with_name(base.name + f'_{index:02d}')
                    if index == 1:
                        path.write_bytes(b'prior file')
                        fixture[path] = b'prior file'
                    else:
                        real_mkdir(path)
                        sentinel = path / 'Sentinel.TXT'
                        sentinel.write_bytes(b'prior directory contents')
                        fixture[sentinel] = b'prior directory contents'
        elif event == 'return' and arg is not None:
            observed['selected'] = arg
    if event == 'return' and module == 'warehouse.launcher' and name == 'warehouse':
        observed['launcher'] = dict(frame.f_locals)

def mkdir(path, *args, **kwargs):
    attempts.append(Path(path))
    if failure is not None:
        raise failure
    return real_mkdir(path, *args, **kwargs)

sys.setprofile(profile)
try:
    with redirect_stdout(out), redirect_stderr(err), patch('os.mkdir', mkdir):
        result = warehouse(['--config', config, '--orders', orders, *options])
finally:
    sys.setprofile(None)

assert result == 1
assert events == ['config', 'orders', 'numeric', 'folder'], events
assert clocks == [datetime], clocks
assert cwds == ['cwd'], cwds
assert _numeric_initialization_state() == observed['numeric']
assert original_inputs == {p: p.read_bytes() for p in Path(config).parent.iterdir() if p.is_file()}
assert '[INFO]' not in out.getvalue() + err.getvalue()
assert 'pending_run_folder' not in out.getvalue() + err.getvalue()
assert Path.cwd() == launch
for path, contents in fixture.items(): assert path.read_bytes() == contents
base = observed['base']
transport = operation != 'none' and case != 'suppressed'
if case in ('permission', 'missing', 'device', 'fatal'):
    assert attempts == [base], attempts
    assert not list(launch.iterdir())
elif case == 'collision':
    assert attempts == [base], attempts
    assert not base.with_name(base.name + '_03').exists()
else:
    selected = observed.get('selected')
    expected = base.with_name(base.name + '_03') if case == 'success_collisions' else base
    assert expected.is_absolute() and expected.is_dir() and not list(expected.iterdir())
    assert attempts == ([base, base.with_name(base.name + '_01'), base.with_name(base.name + '_02'), expected]
                        if case == 'success_collisions' else [base]), attempts
    if case == 'selected' and mode == 'T':
        assert selected is None
    else:
        assert selected == expected
        locals_ = observed['launcher']
        assert locals_['output_directory'] is selected
        assert locals_['files'] is observed['self']
        assert locals_['started_at'] is observed['started_at']
        assert locals_['orders'] is observed['orders']
        assert locals_['config'] is observed['config']
        assert ActivityLog(selected, out).run_directory is selected
        assert get_type_hints(ActivityLog.__init__)['run_directory'] is Path
        assert get_type_hints(WarehouseApplication)['output_directory'] is Path
        assert get_type_hints(WarehouseApplication.__init__)['output_directory'] is Path

if operation != 'unavailable':
    text = err.getvalue()
    if transport:
        assert stream.hit, (target, out.getvalue(), text)
        fallback = [line for line in text.splitlines() if 'reason=console_logging_failed' in line]
        assert len(fallback) == 1, text
        assert 'last_confirmed_trace_line=n/a' in fallback[0]
        assert 'numeric_initialization=' in fallback[0]
        if case in ('selected', 'pending'):
            assert f'run_directory={base}' in fallback[0], fallback
            assert fallback[0].count('run_directory=') == 1
        else:
            assert f'path={base}' in fallback[0], fallback
        if case == 'fatal':
            assert 'startup_failure=run_folder_failed' in fallback[0]
            assert str(failure) in fallback[0]
    elif case in ('permission', 'missing', 'device'):
        assert text.count('[ERROR]') == 1 and 'reason=run_folder_failed' in text
        assert f'path={base}' in text and str(failure) in text
        assert text.count(' path=') == 1 and ' path=n/a' not in text
    else:
        assert 'reason=pending_simulation' in text
        assert text.count('[ERROR]') == 1
        assert f'run_directory={observed["selected"]}' in text
        pending_error = next(line for line in text.splitlines() if line.startswith('[ERROR]'))
        assert pending_error.count('run_directory=') == 1
        assert text.count('[WARNING]') == (3 if case == 'success_collisions' else 0)
        selected_lines = [line for line in out.getvalue().splitlines() if 'function=output_writer.' in line]
        assert len(selected_lines) == (1 if mode == 'T' else 0), selected_lines
        if case == 'suppressed': assert not stream.hit
    if mode != 'T': assert out.getvalue() == ''
'''


class RunDirectoryStartupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.config, _ = write_configuration(self.root / 'Elsewhere Mixed Case Inputs')
        self.orders = self.config.parent / 'Orders.TXT'
        self.orders.write_text('\nOrder Model BLUE\n\nOrder Unknown Purple', encoding='utf-8')

    def run_child(self, mode: str, case: str, operation: str = 'none') -> None:
        launch = self.enterContext(tempfile.TemporaryDirectory(prefix='Mixed Case Launch ', dir=self.root))
        result = subprocess.run(
            [sys.executable, '-c', textwrap.dedent(STARTUP_PROBE), str(self.config),
             str(self.orders), mode, case, operation], cwd=launch,
            env={**os.environ, 'PYTHONPATH': str(PROJECT_ROOT)},
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_real_startup_retains_actual_dependencies_and_creates_after_preflight(self) -> None:
        for mode in ('omitted', 'F', 'T'):
            for case in ('success', 'success_collisions'):
                with self.subTest(mode=mode, case=case):
                    self.run_child(mode, case)

    def test_mkdir_failures_stop_after_one_attempt_and_preserve_numeric_state(self) -> None:
        for mode in ('omitted', 'F', 'T'):
            for case in ('permission', 'missing', 'device'):
                with self.subTest(mode=mode, case=case):
                    self.run_child(mode, case)

    def test_real_handler_failures_at_folder_and_launcher_boundaries(self) -> None:
        for mode in ('omitted', 'F', 'T'):
            for case in ('collision', 'pending', 'fatal', *(['selected'] if mode == 'T' else [])):
                for operation in ('write', 'flush', 'short', 'closed', 'collision_shaped'):
                    with self.subTest(mode=mode, case=case, operation=operation):
                        self.run_child(mode, case, operation)

    def test_unavailable_stderr_still_returns_nonzero_at_new_boundaries(self) -> None:
        for mode in ('omitted', 'F', 'T'):
            for case in ('collision', 'pending', 'fatal', *(['selected'] if mode == 'T' else [])):
                with self.subTest(mode=mode, case=case):
                    self.run_child(mode, case, 'unavailable')

    def test_suppressed_selected_debug_never_touches_a_failing_stream(self) -> None:
        for mode in ('omitted', 'F'):
            for operation in ('write', 'flush', 'short', 'closed'):
                with self.subTest(mode=mode, operation=operation):
                    self.run_child(mode, 'suppressed', operation)
