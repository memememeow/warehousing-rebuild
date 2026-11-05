"""Declare complete order-file grammar validation before mutable state exists.

Nonblank lines contain the case-insensitive Order keyword, model and colour.
Parsing preserves arrival order, duplicates, display spelling and source line
numbers; product acceptance remains the registry's responsibility. OrderLine
stores source line number, model and colour in that order.

File summaries and useful rejection/source evidence use DEBUG under --debug T;
the launcher reports fatal input problems once at ERROR. Emit no INFO here.
"""

from __future__ import annotations

import logging
from io import StringIO
from pathlib import Path

logger = logging.getLogger("warehouse")

type OrderLine = tuple[int, str, str]


class OrderReader:
    """Separate fatal order syntax validation from recoverable product rejection."""

    def read_orders(self, path: Path) -> tuple[OrderLine, ...]:
        """Validate all lines and return nonblank orders in their original order."""
        try:
            contents = path.read_bytes()
        except OSError as error:
            logger.debug(
                "function=order_reader.read_orders action=rejected "
                "reason=read_failed path=%s error=%r", path, error,
            )
            raise OSError(f"orders path={str(path)!r}; cannot read orders: {error}") from error
        try:
            text = contents.decode("utf-8")
        except UnicodeDecodeError as error:
            # Decoding the complete byte string makes this a file offset, not
            # an offset within an unknown TextIOWrapper decoder buffer.
            logger.debug(
                "function=order_reader.read_orders action=rejected "
                "reason=invalid_utf8 path=%s byte_offset=%d byte_end=%d error=%r",
                path, error.start, error.end, error.reason,
            )
            raise ValueError(
                f"orders path={str(path)!r}; invalid UTF-8 "
                f"byte_offset={error.start} byte_end={error.end} (zero-based): {error.reason}"
            ) from error
        orders: list[OrderLine] = []
        line_count = 0
        # Match text-file universal newlines without splitting other whitespace
        # such as vertical tabs, which belong to the three-field grammar.
        with StringIO(text, newline=None) as lines:
            for line_count, line in enumerate(lines, start=1):
                order = self.parse_order(line, path, line_count)
                if order is not None:
                    orders.append(order)
        logger.debug(
            "function=order_reader.read_orders action=validated "
            "path=%s byte_count=%d line_count=%d order_count=%d blank_count=%d",
            path, len(contents), line_count, len(orders), line_count - len(orders),
        )
        return tuple(orders)

    def parse_order(
        self, text: str, source: Path, line_number: int
    ) -> OrderLine | None:
        """Parse exactly three fields, ignore blank lines and retain source context."""
        fields = text.split()
        if not fields:
            return None
        if len(fields) != 3 or fields[0].casefold() != "order":
            logger.debug(
                "function=order_reader.parse_order action=rejected "
                "reason=invalid_grammar path=%s line=%d field_count=%d fields=%r",
                source, line_number, len(fields), fields,
            )
            raise ValueError(
                f"orders path={str(source)!r} line={line_number}; "
                f"expected Order <model> <colour> (three fields); observed fields={fields!r}"
            )
        return line_number, fields[1], fields[2]
