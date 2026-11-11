"""Verify retained request identity and run-folder times; reserve ID allocation.

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

from UnitTests import isolated_console_logging
from warehouse.domain.fulfillment_request import FulfillmentRequest
from warehouse.domain.order import Order
from warehouse.domain.request_status import RequestStatus


class RequestIdentityAndSkuTests(unittest.TestCase):
    """Supplied identifiers are data; allocation and time formatting remain Step 14."""

    def setUp(self) -> None:
        self.enterContext(isolated_console_logging())

    def test_id_and_sequence_are_retained_independently_without_parsing(self) -> None:
        order = Order('O0037', 'Model', 'Blue', '001', '090')
        for request_id, sequence in (('R202611110930-021', 37), ('unchanged identity', 2)):
            request = FulfillmentRequest(request_id, sequence, (order,), ('001', '090'))
            self.assertEqual(request.id, request_id)
            self.assertEqual(request.sequence, sequence)
            request.transition_to(RequestStatus.WAITING_FOR_PICKING)
            self.assertEqual(request.id, request_id)
            self.assertEqual(request.sequence, sequence)
            self.assertIs(request.orders[0], order)

    def test_loading_tuples_for_one_two_three_and_four_orders_preserve_occurrences(self) -> None:
        pairs = (('001', '090'), ('002', '080'), ('001', '090'), ('004', '060'))
        expected = {
            1: ('001', '090'),
            2: ('001', '090', '002', '080'),
            3: ('001', '090', '002', '080', '001', '090'),
            4: ('001', '090', '002', '080', '001', '090', '004', '060'),
        }
        for count in (1, 2, 3, 4):
            with self.subTest(count=count):
                orders = tuple(Order(f'O{i + 1:04d}', 'MoDeL', 'bLuE', *pair)
                               for i, pair in enumerate(pairs[:count]))
                evidence = tuple(front for front, back in pairs[:count]) + tuple(
                    back for front, back in pairs[:count])
                request = FulfillmentRequest('R202611110930-021', 21, orders, evidence)
                stored_orders, stored_evidence = request.orders, request.correct_sku_order
                for repeat in range(3):
                    self.assertEqual(request.loading_skus(), expected[count], repeat)
                    self.assertIs(request.orders, stored_orders)
                    self.assertIs(request.correct_sku_order, stored_evidence)
                    self.assertEqual(request.correct_sku_order, evidence)
                    for original, retained in zip(orders, request.orders, strict=True):
                        self.assertIs(original, retained)
                        self.assertEqual((retained.model, retained.colour), ('MoDeL', 'bLuE'))

    def test_loading_uses_stored_evidence_with_repeats_across_both_halves(self) -> None:
        order = Order('O0001', 'Model', 'Blue', '7', '8')
        request = FulfillmentRequest('kept', 1, (order, order, order),
                                     ('001', '001', '002', '001', '002', '001'))
        self.assertEqual(request.loading_skus(), ('001', '001', '001', '002', '002', '001'))
        self.assertEqual(request.correct_sku_order, ('001', '001', '002', '001', '002', '001'))
        self.assertEqual((order.front_sku, order.back_sku), ('7', '8'))

    def test_unpaired_evidence_is_not_silently_truncated(self) -> None:
        order = Order('O0001', 'Model', 'Blue', '1', '2')
        request = FulfillmentRequest('kept', 1, (order,), ('1', '2', '3'))
        evidence = request.correct_sku_order
        with self.assertRaises(ValueError):
            request.loading_skus()
        self.assertIs(request.correct_sku_order, evidence)
        self.assertIs(request.orders[0], order)
        self.assertIs(request.status, RequestStatus.CREATED)


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
