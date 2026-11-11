"""Exercise Truck events through actual checked handlers and nested scopes.

Direct Truck/request scopes prove logging boundaries only. Ordered loading,
LOADED/DISPATCHED integration and dispatch still require the real Step 19 owner.
"""

from __future__ import annotations

from UnitTests.test_trucks import TruckProcessTestCase


class TruckDiagnosticTests(TruckProcessTestCase):
    def test_routine_construction_and_capacity_queries_remain_silent(self) -> None:
        self.run_fresh("""
            with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                _configure_console_logging(logging.DEBUG)
                truck = Truck('T001')
                for repeat in range(3):
                    assert truck.requests == [] and truck.capacity_requests == 5
                    assert truck.is_full() is False
                assert out.getvalue() == err.getvalue() == ''
        """)

    def test_real_cli_modes_emit_complete_events_once_without_changing_contents(self) -> None:
        self.run_fresh("""
            outcomes = []
            for options in ([], ['--debug', 'F'], ['--debug', 'T']):
                root_out = io.StringIO()
                root_handler = logging.StreamHandler(root_out)
                root_logger = logging.getLogger()
                root_logger.addHandler(root_handler)
                try:
                    with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                        args = _parse_arguments(['--config', 'unused', '--orders', 'unused', *options])
                        level = logging.DEBUG if args.debug == 'T' else logging.INFO
                        _configure_console_logging(level)
                        logger = logging.getLogger('warehouse')
                        assert logger.level == level and all(h.level == level for h in logger.handlers)
                        truck = Truck('T013')
                        requests = (truck_request(21, 4), truck_request(22, 1), truck_request(23, 3))
                        truck.record_request(requests[0])
                        with case.assertRaises(ValueError): truck.record_request(requests[0])
                        truck.record_request(requests[1])
                        with case.assertRaises(ValueError): truck.record_request(requests[0])
                        with case.assertRaises(ValueError): truck.record_request(requests[2])
                        lines = out.getvalue().splitlines()
                        assert len(lines) == (5 if args.debug == 'T' else 0)
                        assert len(lines) == len(set(lines))
                        assert out.getvalue().count('\\n') == len(lines)
                        assert err.getvalue() == root_out.getvalue() == ''
                        assert '[INFO]' not in out.getvalue()
                        expected = (
                            ('recorded', 21, 4, 0, 1, None),
                            ('rejected', 21, 4, 1, 1, 'duplicate_request'),
                            ('recorded', 22, 1, 1, 2, None),
                            ('rejected', 21, 4, 2, 2, 'duplicate_request'),
                            ('rejected', 23, 3, 2, 2, 'capacity_exceeded'),
                        )
                        for line, (action, sequence, count, before, after, reason) in zip(lines, expected):
                            assert line.startswith(f'[DEBUG] function=truck.record_request action={action} ')
                            evidence = (
                                'truck_id=T013', f'request_id=R202611110930-{sequence:03d}',
                                f'request_sequence={sequence}', 'capacity_requests=2',
                                f'request_count_before={before}', f'request_count_after={after}',
                                'status_before=created', 'status_after=created',
                                'order_ids=' + repr(tuple(f'O{sequence:04d}-{i}' for i in range(count))),
                            )
                            for item in evidence: assert line.count(item) == 1, (item, line)
                            if reason: assert f'reason={reason}' in line
                            assert '=n/a' not in line and 'action=entry' not in line
                        outcomes.append((truck.id, tuple(r.id for r in truck.requests),
                                         tuple(r.status for r in requests), truck.is_full()))
                finally:
                    root_logger.removeHandler(root_handler)
                    root_handler.close()
            assert outcomes[0] == outcomes[1] == outcomes[2]
        """, 2)

    def test_suppressed_debug_neither_builds_records_nor_formats_evidence(self) -> None:
        self.run_fresh("""
            class UnformattableString(str):
                def __str__(self): raise AssertionError('suppressed evidence formatted')
                def __repr__(self): raise AssertionError('suppressed evidence formatted')
            for level in (logging.INFO, logging.WARNING):
                with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                    _configure_console_logging(level)
                    order = Order(UnformattableString('O1'), 'Model', 'Blue', '001', '002')
                    request = FulfillmentRequest('supplied', 21, (order,), ('001', '002'))
                    truck = Truck('T001')
                    with (patch('warehouse.launcher._format_context', side_effect=AssertionError('formatted')),
                          patch.object(logging.getLogger('warehouse'), 'debug', side_effect=AssertionError('built record'))):
                        with buffered_diagnostics():
                            truck.record_request(request)
                            with case.assertRaises(ValueError): truck.record_request(request)
                    assert truck.requests[0] is request and request.status is RequestStatus.CREATED
                    assert out.getvalue() == err.getvalue() == ''
        """)

    def test_real_transport_failures_preserve_rejections_and_committed_appends(self) -> None:
        self.run_fresh("""
            from UnitTests.test_order_logging import FailingDiagnosticStream
            for scenario in ('recorded', 'duplicate_room', 'duplicate_full', 'overflow'):
                for operation in ('write', 'flush', 'short', 'closed'):
                    logging.getLogger('warehouse').setLevel(logging.INFO)
                    truck = Truck('T013')
                    requests = tuple(truck_request(number, count) for number, count in ((21, 4), (22, 1), (23, 3)))
                    if scenario != 'recorded': truck.record_request(requests[0])
                    if scenario in ('duplicate_full', 'overflow'): truck.record_request(requests[1])
                    candidate = requests[2] if scenario == 'overflow' else requests[0]
                    before = truck_snapshot(truck, requests)
                    request_before = tuple(request_snapshot(request) for request in requests)
                    event = 'recorded' if scenario == 'recorded' else 'rejected'
                    stream = FailingDiagnosticStream(f'function=truck.record_request action={event}', operation)
                    with (redirect_stdout(stream), redirect_stderr(io.StringIO()) as err,
                          patch.object(logging, 'raiseExceptions', False)):
                        _configure_console_logging(logging.DEBUG)
                        with case.assertRaises(ValueError if operation in ('flush', 'closed') else OSError) as caught:
                            truck.record_request(candidate)
                    assert stream.hit and caught.exception.warehouse_logging_failure
                    assert caught.exception.__cause__ is None and err.getvalue() == ''
                    if operation in ('write', 'flush'): assert caught.exception is stream.failure
                    assert tuple(request_snapshot(request) for request in requests) == request_before
                    if scenario == 'recorded':
                        assert len(truck.requests) == 1 and truck.requests[0] is candidate
                        after = truck_snapshot(truck, requests)
                        assert after[:6] == before[:6] and after[7] == before[7]
                        assert after[6] == (request_before[0],)
                    else:
                        assert truck_snapshot(truck, requests) == before
        """, 2)

    def test_direct_nested_scopes_defer_formatting_until_all_public_changes_finish(self) -> None:
        self.run_fresh("""
            from warehouse.launcher import _format_context
            request = truck_request(21, 4, RequestStatus.AWAITING_LOAD_RESCAN)
            truck = Truck('T013')
            logger = logging.getLogger('warehouse')
            filters = logger.filters[:]
            with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                _configure_console_logging(logging.DEBUG)
                with patch('warehouse.launcher._format_context', wraps=_format_context) as formatting:
                    with buffered_diagnostics():
                        with buffered_diagnostics():
                            truck.record_request(request)
                            request.transition_to(RequestStatus.LOADED)
                            formatting.assert_not_called()
                            assert out.getvalue() == ''
                        request.transition_to(RequestStatus.DISPATCHED)
                        assert truck.requests[0] is request and request.status is RequestStatus.DISPATCHED
                        formatting.assert_not_called()
                        assert out.getvalue() == ''
                    assert formatting.call_count == 3
                lines = out.getvalue().splitlines()
                assert len(lines) == 3 and len(set(lines)) == 3
                assert 'function=truck.record_request action=recorded' in lines[0]
                assert 'status_after=awaiting_load_rescan' in lines[0]
                assert 'status_after=loaded' in lines[1] and 'status_after=dispatched' in lines[2]
                assert err.getvalue() == '' and logger.filters == filters
        """)

    def test_direct_nested_transport_faults_happen_after_body_and_restore_filters(self) -> None:
        self.run_fresh("""
            from UnitTests.test_order_logging import FailingDiagnosticStream
            logger = logging.getLogger('warehouse')
            for target in ('function=truck.record_request', 'function=fulfillment_request.transition_to'):
                for operation in ('write', 'flush', 'short', 'closed'):
                    logger.setLevel(logging.INFO)
                    request = truck_request(21, 3, RequestStatus.AWAITING_LOAD_RESCAN)
                    before_request = request_snapshot(request)
                    truck = Truck('T013')
                    filters = logger.filters[:]
                    stream = FailingDiagnosticStream(target, operation)
                    finished = False
                    with (redirect_stdout(stream), redirect_stderr(io.StringIO()) as err,
                          patch.object(logging, 'raiseExceptions', False)):
                        _configure_console_logging(logging.DEBUG)
                        with case.assertRaises(ValueError if operation in ('flush', 'closed') else OSError) as caught:
                            with buffered_diagnostics():
                                with buffered_diagnostics():
                                    truck.record_request(request)
                                    request.transition_to(RequestStatus.LOADED)
                                request.transition_to(RequestStatus.DISPATCHED)
                                finished = True
                                assert not stream.hit
                    assert finished and stream.hit and caught.exception.warehouse_logging_failure
                    if operation in ('write', 'flush'): assert caught.exception is stream.failure
                    assert err.getvalue() == '' and logger.filters == filters
                    assert len(truck.requests) == 1 and truck.requests[0] is request
                    after_request = request_snapshot(request)
                    assert before_request[:3] + before_request[4:] == after_request[:3] + after_request[4:]
                    assert request.status is RequestStatus.DISPATCHED
                    with redirect_stdout(io.StringIO()) as next_out, redirect_stderr(io.StringIO()):
                        _configure_console_logging(logging.DEBUG)
                        with buffered_diagnostics(): truck.record_request(truck_request(22, 1))
                        assert len(next_out.getvalue().splitlines()) == 1
                        assert 'request_sequence=22' in next_out.getvalue()
        """)

    def test_enabled_evidence_format_failure_follows_the_complete_direct_scope(self) -> None:
        self.run_fresh("""
            finished = False
            failure = ValueError('order ID formatting failed')
            class DeferredOrderId(str):
                def __repr__(self):
                    assert finished, 'formatted inside related changes'
                    raise failure
            order = Order(DeferredOrderId('O1'), 'Model', 'Blue', '001', '002')
            request = FulfillmentRequest('supplied', 21, (order,), ('001', '002'))
            truck = Truck('T001')
            with (redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err,
                  patch.object(logging, 'raiseExceptions', False)):
                _configure_console_logging(logging.DEBUG)
                with case.assertRaises(ValueError) as caught:
                    with buffered_diagnostics():
                        truck.record_request(request)
                        request.transition_to(RequestStatus.WAITING_FOR_PICKING)
                        finished = True
                assert caught.exception is failure and failure.warehouse_logging_failure
                assert truck.requests[0] is request and request.status is RequestStatus.WAITING_FOR_PICKING
                assert out.getvalue() == err.getvalue() == ''
        """)

    def test_exceptional_direct_scope_retains_append_and_delivers_rejection_once(self) -> None:
        self.run_fresh("""
            truck = Truck('T001')
            request = truck_request(21, 1)
            before_request = request_snapshot(request)
            with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                _configure_console_logging(logging.DEBUG)
                filters = logging.getLogger('warehouse').filters[:]
                with case.assertRaisesRegex(ValueError, 'duplicate_request'):
                    with buffered_diagnostics():
                        truck.record_request(request)
                        truck.record_request(request)
                lines = out.getvalue().splitlines()
                assert len(lines) == 2 and 'action=recorded' in lines[0] and 'action=rejected' in lines[1]
                assert request_snapshot(request) == before_request and truck.requests[0] is request
                assert logging.getLogger('warehouse').filters == filters and err.getvalue() == ''
        """)
