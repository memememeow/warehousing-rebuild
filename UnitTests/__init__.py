"""Reserve standard-library tests for the Phase 1 warehouse contracts.

Owner tests will use deterministic fixtures and isolated filesystem boundaries.
Executable CLI and console tests cover steps 01–02; other owner modules remain
placeholders and must not count as implementation completion.

Console tests cover thresholds, routing and transport failures. Exact INFO/trace
equivalence remains deferred to step 21 with ActivityLog.record_command.
"""

from __future__ import annotations

import logging
import unittest
from collections.abc import Iterator
from contextlib import contextmanager


def assert_callable_traces(
    case: unittest.TestCase, output: str, callables: tuple[str, ...]
) -> None:
    """Require qualified entry/results without fixing the number of DEBUG records."""
    lines = output.splitlines()
    for function in callables:
        for action in ("entry", "returned"):
            with case.subTest(function=function, action=action):
                matching = [
                    line for line in lines
                    if line.startswith(f"[DEBUG] function={function} action={action} ")
                ]
                case.assertTrue(matching, f"Missing {function} {action}:\n{output}")


@contextmanager
def isolated_console_logging() -> Iterator[logging.Logger]:
    """Keep each test's warehouse handlers and thresholds out of other tests."""
    logger = logging.getLogger("warehouse")
    original_handlers = logger.handlers[:]
    original_filters = logger.filters[:]
    original_level = logger.level
    original_propagate = logger.propagate
    original_disabled = logger.disabled
    logger.handlers.clear()
    logger.filters.clear()
    logger.setLevel(logging.WARNING)
    logger.propagate = False
    logger.disabled = False
    try:
        yield logger
    finally:
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()
        logger.handlers[:] = original_handlers
        logger.filters[:] = original_filters
        logger.setLevel(original_level)
        logger.propagate = original_propagate
        logger.disabled = original_disabled
