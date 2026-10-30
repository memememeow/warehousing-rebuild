"""Declare complete order-file grammar validation before mutable state exists.

Nonblank lines contain the case-insensitive Order keyword, model and colour.
Parsing preserves arrival order, duplicates, display spelling and source line
numbers; product acceptance remains the registry's responsibility. OrderLine
stores source line number, model and colour in that order.
"""

from __future__ import annotations

from pathlib import Path

# TODO: Validate the whole UTF-8 input and report malformed source lines.

type OrderLine = tuple[int, str, str]


class OrderReader:
    """Separate fatal order syntax validation from recoverable product rejection."""

    def read_orders(self, path: Path) -> tuple[OrderLine, ...]:
        """Validate all lines and return nonblank orders in their original order."""
        ...

    def parse_order(
        self, text: str, source: Path, line_number: int
    ) -> OrderLine | None:
        """Parse exactly three fields, ignore blank lines and retain source context."""
        ...
