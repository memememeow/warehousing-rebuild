"""Verify run-folder local times; reserve identifier-allocation coverage.

Future cases verify independent counters, minimum widths, acceptance-only
allocation, aware datetime requirements and daylight-saving transitions.
Alternate configurations and timezone cases require isolated subprocesses.

Step 04 tests exercise ProductCatalog directly, without a registry substitute.
Proof that unknown products consume no order/request numbers, and that accepted
Orders preserve input spelling, remains deferred to the real Step 14 registry.
See StepAudits/step_04_audit.md for the catalog evidence and integration boundaries.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


class RunFolderLocalTimeTests(unittest.TestCase):
    """Each formatting call resolves the OS offset for its own aware instant."""

    def test_dst_transitions_and_repeated_minutes_in_isolated_timezones(self) -> None:
        project = Path(__file__).resolve().parent.parent
        code = textwrap.dedent('''
            import io, logging, time
            from contextlib import redirect_stdout, redirect_stderr
            from datetime import datetime, timezone, timedelta
            from pathlib import Path
            from warehouse.file_io.output_writer import OutputWriter
            from warehouse.launcher import _configure_console_logging
            time.tzset()
            launch = Path.cwd()
            # Before/after spring and both occurrences of the autumn minute.
            instants = [datetime.fromisoformat(value) for value in (
                '2026-03-08T06:59:00+00:00', '2026-03-08T07:00:00+00:00',
                '2026-11-01T05:30:00+00:00', '2026-11-01T06:30:00+00:00')]
            offsets = [instant.astimezone().utcoffset() for instant in instants]
            out, err = io.StringIO(), io.StringIO()
            writer = OutputWriter()
            seen = {}
            with redirect_stdout(out), redirect_stderr(err):
                _configure_console_logging(logging.DEBUG)
                for instant in instants:
                    # Supply a nonlocal offset for the same instant too.
                    for supplied in (instant, instant.astimezone(timezone(timedelta(hours=9, minutes=30)))):
                        name = supplied.astimezone().strftime('warehouse_%Y%m%d%H%M')
                        count = seen.get(name, 0)
                        selected = writer.create_output_directory(launch, supplied)
                        expected = name + (f'_{count:02d}' if count else '')
                        assert selected == launch / expected, (selected, expected)
                        (selected / 'sentinel').write_text(expected)
                        seen[name] = count + 1
            import os
            if os.environ['TZ'].startswith('EST'):
                assert offsets[0] != offsets[1] and offsets[2] != offsets[3], offsets
                assert instants[2].astimezone().strftime('%Y%m%d%H%M') == instants[3].astimezone().strftime('%Y%m%d%H%M')
                assert (launch / 'warehouse_202603080159').is_dir()
                assert (launch / 'warehouse_202603080300').is_dir()
                assert (launch / 'warehouse_202611010130_03').is_dir()
            for folder in launch.iterdir():
                assert (folder / 'sentinel').read_text() == folder.name
            assert len(out.getvalue().splitlines()) == 8
            assert all(line.startswith('[DEBUG] function=output_writer.create_output_directory action=created path=') for line in out.getvalue().splitlines())
            assert len(err.getvalue().splitlines()) == sum(count * (count-1)//2 for count in seen.values())
            assert '[INFO]' not in out.getvalue() + err.getvalue()
        ''')
        for zone in ('UTC0', 'EST5EDT,M3.2.0,M11.1.0', 'NPT-5:45'):
            with self.subTest(zone=zone), tempfile.TemporaryDirectory() as launch:
                result = subprocess.run(
                    [sys.executable, '-c', code], cwd=launch,
                    env={**os.environ, 'PYTHONPATH': str(project), 'TZ': zone},
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


# TODO (step 14): Check real ID counters, widths and aware request timestamps.
