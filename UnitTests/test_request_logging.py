"""Exercise request events and commit buffering through real checked handlers.

These scopes prove diagnostic deferral, not the still-stubbed workflow commits.
"""

from __future__ import annotations

import io
import logging
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from UnitTests import isolated_console_logging
from UnitTests.test_order_logging import FailingDiagnosticStream
from UnitTests.test_workflows import request_at, request_snapshot
from warehouse.domain.fulfillment_request import FulfillmentRequest
from warehouse.domain.order import Order
from warehouse.domain.request_status import RequestStatus
from warehouse.file_io.activity_log import buffered_diagnostics
from warehouse.launcher import _configure_console_logging, _format_context, _parse_arguments


class RequestDiagnosticTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = self.enterContext(isolated_console_logging())

    def test_silent_construction_properties_and_loading_query(self) -> None:
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            _configure_console_logging(logging.DEBUG)
            request = request_at(RequestStatus.CREATED)
            for repeat in range(3):
                request_snapshot(request)
                request.loading_skus()
            self.assertEqual(out.getvalue(), '')
            self.assertEqual(err.getvalue(), '')

    def test_actual_cli_thresholds_route_each_event_once_with_full_context(self) -> None:
        for options in ([], ['--debug', 'F'], ['--debug', 'T']):
            with (self.subTest(options=options), redirect_stdout(io.StringIO()) as out,
                  redirect_stderr(io.StringIO()) as err):
                arguments = _parse_arguments(['--config', 'unused', '--orders', 'unused', *options])
                level = logging.DEBUG if arguments.debug == 'T' else logging.INFO
                _configure_console_logging(level)
                self.assertEqual(self.logger.level, level)
                self.assertTrue(all(handler.level == level for handler in self.logger.handlers))
                request = request_at(RequestStatus.CREATED)
                with self.assertRaises(ValueError):
                    request.transition_to(RequestStatus.PICKING)
                request.transition_to(RequestStatus.WAITING_FOR_PICKING)
                lines = out.getvalue().splitlines()
                self.assertEqual(len(lines), 2 if arguments.debug == 'T' else 0)
                self.assertEqual(len(lines), len(set(lines)))
                self.assertEqual(err.getvalue(), '')
                if lines:
                    self.assertTrue(lines[0].startswith(
                        '[DEBUG] function=fulfillment_request.transition_to action=rejected '))
                    self.assertTrue(lines[1].startswith(
                        '[DEBUG] function=fulfillment_request.transition_to action=transitioned '))
                    self.assertIn('reason=illegal_transition', lines[0])
                    for line in lines:
                        for context in ('request_id=R202611110930-021', 'request_sequence=21',
                                        "order_ids=('O0137', 'O0141')", 'status_before=created',
                                        "sku_evidence=('001', '001', '090', '090')"):
                            self.assertIn(context, line)
                        self.assertNotIn('=n/a', line)
                        self.assertNotIn('[INFO]', line)
                    self.assertIn('requested_status=picking', lines[0])
                    self.assertIn('status_after=created', lines[0])
                    self.assertIn('status_after=waiting_for_picking', lines[1])
                    self.assertEqual(out.getvalue().count('\n'), 2)

    def test_suppressed_debug_does_not_format_evidence(self) -> None:
        class UnformattableString(str):
            def __str__(self) -> str:
                raise AssertionError('suppressed evidence formatted')

            def __repr__(self) -> str:
                raise AssertionError('suppressed evidence formatted')

        for level in (logging.INFO, logging.WARNING):
            with (self.subTest(level=level), redirect_stdout(io.StringIO()) as out,
                  redirect_stderr(io.StringIO()) as err):
                _configure_console_logging(level)
                order = Order(UnformattableString('O0137'), 'Model', 'Blue', '001', '090')
                request = FulfillmentRequest('retained', 21, (order,),
                                             (UnformattableString('001'), UnformattableString('090')))
                with patch('warehouse.launcher._format_context', side_effect=AssertionError('formatted')):
                    with buffered_diagnostics():
                        with self.assertRaises(ValueError):
                            request.transition_to(RequestStatus.PICKING)
                        request.transition_to(RequestStatus.WAITING_FOR_PICKING)
                self.assertIs(request.status, RequestStatus.WAITING_FOR_PICKING)
                self.assertEqual(out.getvalue() + err.getvalue(), '')

    def test_transition_context_keeps_all_four_order_ids_and_repeated_skus(self) -> None:
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            _configure_console_logging(logging.DEBUG)
            orders = tuple(Order(order_id, 'Model', 'Blue', '001', '090')
                           for order_id in ('O0137', 'O0141', 'O0159', 'O0163'))
            evidence = ('001', '001', '001', '001', '090', '090', '090', '090')
            request = FulfillmentRequest('R202611110930-021', 21, orders, evidence)
            request.transition_to(RequestStatus.WAITING_FOR_PICKING)
            lines = out.getvalue().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertIn("order_ids=('O0137', 'O0141', 'O0159', 'O0163')", lines[0])
            self.assertIn(f'sku_evidence={evidence}', lines[0])
            self.assertEqual(err.getvalue(), '')

    def test_actual_transport_failures_leave_rejected_or_committed_status_accurate(self) -> None:
        for action, target in (('rejected', RequestStatus.PICKING),
                               ('transitioned', RequestStatus.WAITING_FOR_PICKING)):
            for operation in ('write', 'flush', 'short', 'closed'):
                with self.subTest(action=action, operation=operation):
                    request = request_at(RequestStatus.CREATED)
                    before = request_snapshot(request)
                    stream = FailingDiagnosticStream(
                        f'function=fulfillment_request.transition_to action={action}', operation)
                    with (redirect_stdout(stream), redirect_stderr(io.StringIO()) as err,
                          patch.object(logging, 'raiseExceptions', False)):
                        _configure_console_logging(logging.DEBUG)
                        with self.assertRaises(ValueError if operation in ('flush', 'closed') else OSError) as caught:
                            request.transition_to(target)
                    self.assertTrue(stream.hit)
                    self.assertTrue(caught.exception.warehouse_logging_failure)
                    if operation in ('write', 'flush'):
                        self.assertIs(caught.exception, stream.failure)
                    after = request_snapshot(request)
                    self.assertEqual(before[:3] + before[4:], after[:3] + after[4:])
                    self.assertIs(request.status, RequestStatus.CREATED if action == 'rejected' else target)
                    self.assertEqual(err.getvalue(), '')

    def test_nested_scopes_deliver_ordered_raw_records_only_after_outer_exit(self) -> None:
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            _configure_console_logging(logging.DEBUG)
            request = request_at(RequestStatus.CREATED)
            filters = self.logger.filters[:]
            with patch('warehouse.launcher._format_context', wraps=_format_context) as formatting:
                with buffered_diagnostics():
                    request.transition_to(RequestStatus.WAITING_FOR_PICKING)
                    with buffered_diagnostics():
                        request.transition_to(RequestStatus.PICKING)
                        self.assertEqual(out.getvalue(), '')
                    request.transition_to(RequestStatus.READY_FOR_SEQUENCING)
                    self.assertEqual(out.getvalue(), '')
                    formatting.assert_not_called()
                self.assertEqual(formatting.call_count, 3)
            lines = out.getvalue().splitlines()
            self.assertEqual(len(lines), 3)
            for line, target in zip(lines, (RequestStatus.WAITING_FOR_PICKING, RequestStatus.PICKING,
                                             RequestStatus.READY_FOR_SEQUENCING), strict=True):
                self.assertIn(f'status_after={target}', line)
            self.assertEqual(self.logger.filters, filters)
            self.assertEqual(err.getvalue(), '')

    def test_nested_transport_failure_occurs_after_all_body_changes_and_cleans_filters(self) -> None:
        for operation in ('write', 'flush', 'short', 'closed'):
            with self.subTest(operation=operation):
                request = request_at(RequestStatus.CREATED)
                stream = FailingDiagnosticStream('function=fulfillment_request.transition_to', operation)
                finished = False
                filters = self.logger.filters[:]
                with (redirect_stdout(stream), redirect_stderr(io.StringIO()),
                      patch.object(logging, 'raiseExceptions', False)):
                    _configure_console_logging(logging.DEBUG)
                    with self.assertRaises((OSError, ValueError)) as caught:
                        with buffered_diagnostics():
                            request.transition_to(RequestStatus.WAITING_FOR_PICKING)
                            with buffered_diagnostics():
                                request.transition_to(RequestStatus.PICKING)
                            request.transition_to(RequestStatus.READY_FOR_SEQUENCING)
                            finished = True
                            self.assertFalse(stream.hit)
                self.assertTrue(finished and stream.hit)
                self.assertTrue(caught.exception.warehouse_logging_failure)
                self.assertIs(request.status, RequestStatus.READY_FOR_SEQUENCING)
                self.assertEqual(self.logger.filters, filters)
                # A failed flush must not leave the next independent scope buffered.
                with redirect_stdout(io.StringIO()) as next_out, redirect_stderr(io.StringIO()):
                    _configure_console_logging(logging.DEBUG)
                    with buffered_diagnostics():
                        request.transition_to(RequestStatus.SEQUENCING)
                    self.assertEqual(len(next_out.getvalue().splitlines()), 1)

    def test_exceptional_scope_exit_keeps_prior_commit_and_rejection_evidence(self) -> None:
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
            _configure_console_logging(logging.DEBUG)
            filters = self.logger.filters[:]
            request = request_at(RequestStatus.CREATED)
            with self.assertRaises(ValueError):
                with buffered_diagnostics():
                    request.transition_to(RequestStatus.WAITING_FOR_PICKING)
                    request.transition_to(RequestStatus.DISPATCHED)
            self.assertIs(request.status, RequestStatus.WAITING_FOR_PICKING)
            lines = out.getvalue().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertIn('action=transitioned', lines[0])
            self.assertIn('action=rejected', lines[1])
            self.assertEqual(self.logger.filters, filters)
