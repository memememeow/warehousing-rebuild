"""Validate JSON configuration and its three CSV references.

Preflight owns normalized-key collision detection, catalog and location
coverage, stock constraints, worker roles and identifier settings. Validation
must preserve display spelling and report source context before state exists.

The launcher configures console verbosity before loading. Configuration and
file-read entry traces include supplied/resolved paths and expected headers at
DEBUG under --debug T. Parser, token and field helpers stay silent on success;
legacy normalization uses WARNING and fatal preflight problems use ERROR.
Emit no INFO and keep the launcher-only debug setting outside JSON and WarehouseConfig.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from collections.abc import Mapping
from datetime import datetime
from functools import partial
from pathlib import Path
from unicodedata import decimal

from warehouse.configuration.warehouse_config import WarehouseConfig
from warehouse.domain.location import Location, LocationMap
from warehouse.domain.product_catalog import ProductCatalog
from warehouse.domain.worker_type import WorkerType
from warehouse.file_io.activity_log import _diagnostic_text

logger = logging.getLogger("warehouse")


class ConfigLoader:
    """Publish one immutable configuration after complete reference validation."""

    def __init__(self, working_directory: Path | None = None) -> None:
        self._working_directory = (
            working_directory if working_directory is not None else Path.cwd()
        )

    def load_config(self, config_path: Path) -> WarehouseConfig:
        """Validate JSON and referenced CSVs; raise ValueError or OSError on failure."""
        logger.debug(
            "function=config_loader.load_config "
            "action=entry supplied=%r", config_path,
            extra={"source": config_path, "path": config_path},
        )
        resolved = _resolve_config_path(config_path)
        source = f"config supplied={str(config_path)!r} resolved={str(resolved)!r}"
        document = _read_json(resolved, source)
        files = _section(document, "files", source)
        paths = _reference_paths(resolved, files, source)
        sources = {
            field: (
                f"{source}; JSON field files.{field} supplied={files[field]!r} "
                f"resolved={str(path)!r}"
            )
            for field, path in paths.items()
        }

        catalog = _read_products(paths["translation"], sources["translation"])
        location_map, zones = _read_locations(paths["location_map"], sources["location_map"])
        _validate_product_locations(catalog, location_map, sources)
        initial_stock = _read_stock(
            paths["initial_inventory"], sources["initial_inventory"], location_map, zones,
        )

        employees = _employees(_section(document, "employees", source), source)
        warehouse = _section(document, "warehouse", source)
        identifiers = _section(document, "identifiers", source)
        settings = _settings(warehouse, identifiers, source)
        self._warn_ignored_settings(files, identifiers, source)

        return WarehouseConfig(
            translation_path=paths["translation"],
            location_map_path=paths["location_map"],
            initial_inventory_path=paths["initial_inventory"],
            catalog=catalog,
            location_map=location_map,
            initial_stock=initial_stock,
            employees=employees,
            **settings,
        )

    def _warn_ignored_settings(
        self,
        files: Mapping[str, object],
        identifiers: Mapping[str, object],
        source: str,
    ) -> None:
        if "output_root" in files:
            logger.warning(
                "function=config_loader.load_config "
                "action=ignored JSON field=files.output_root value=%s; output_parent=%s",
                _diagnostic_text(files["output_root"], conversion="r"), self._working_directory,
                extra={"source": source},
            )

        if "request_timestamp_timezone" in identifiers:
            local_instant = datetime.now().astimezone()
            logger.warning(
                "function=config_loader.load_config "
                "action=ignored JSON field=identifiers.request_timestamp_timezone value=%s; "
                "local_timezone=%s utc_offset=%s local_instant=%s",
                _diagnostic_text(identifiers["request_timestamp_timezone"], conversion="r"),
                local_instant.tzname(), local_instant.strftime("%z"), local_instant.isoformat(),
                extra={"source": source},
            )


def _resolve_config_path(config_path: Path) -> Path:
    try:
        return config_path.resolve()
    except (OSError, ValueError) as error:
        raise ValueError(
            f"config supplied={str(config_path)!r}; resolution failed: {error}"
        ) from error


def _read_json(path: Path, source: str) -> dict[str, object]:
    logger.debug(
        "function=config_loader._read_json action=entry",
        extra={"source": source, "path": path},
    )
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError as error:
        raise ValueError(f"{source}; invalid UTF-8 JSON: {error}") from error
    except OSError as error:
        raise OSError(f"{source}; cannot read JSON: {error}") from error

    # Keep file and decoder failures distinct from diagnostic transport errors.
    # Parse decimal chunks locally without changing process-wide conversion limits.
    try:
        document = json.loads(
            text,
            object_pairs_hook=partial(_json_object, source),
            parse_int=partial(_json_number, source),
            parse_constant=partial(_json_number, source),
        )
    except json.JSONDecodeError as error:
        if getattr(error, "warehouse_logging_failure", False):
            raise
        raise ValueError(
            f"{source}; JSON line={error.lineno} column={error.colno}: {error.msg}"
        ) from error

    if not isinstance(document, dict):
        raise ValueError(f"{source}; JSON root must be an object")
    return document


def _json_object(source: str, pairs: list[tuple[str, object]]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for key, value in pairs:
        name = key.strip().casefold()
        if name in normalized:
            raise ValueError(f"{source}; duplicate JSON key supplied={key!r} normalized={name!r}")

        normalized[name] = value

    return normalized


def _json_number(source: str, token: str) -> int:
    """Parse integers and reject the decoder's nonstandard numeric constants."""
    try:
        value = _decimal_integer(token, source)
    except ValueError as error:
        if getattr(error, "warehouse_logging_failure", False):
            raise
        raise ValueError(f"{source}; invalid JSON number token={token!r}: {error}") from error
    return value


def _reference_paths(
    config_path: Path, files: Mapping[str, object], source: str,
) -> dict[str, Path]:
    return {
        field: _resolve_reference(config_path, field, _required(files, field, source), source)
        for field in ("translation", "location_map", "initial_inventory")
    }


def _resolve_reference(config_path: Path, field: str, supplied: object, source: str) -> Path:
    context = f"{source}; JSON field files.{field} supplied={_diagnostic_text(supplied, conversion='r')}"
    if not isinstance(supplied, str) or not supplied:
        raise ValueError(f"{context}; expected a nonempty path string")

    try:
        resolved = (config_path.parent / supplied).resolve()
    except (OSError, ValueError) as error:
        raise ValueError(f"{context}; path resolution failed: {error}") from error
    return resolved


def _read_products(path: Path, source: str) -> ProductCatalog:
    products: dict[tuple[str, str], tuple[str, str]] = {}
    for line, row in _read_csv(path, source, ("colour", "model", "sku (front)", "sku (back)")):
        context = f"{source}; CSV line={line}"
        product = (row["model"].strip().casefold(), row["colour"].strip().casefold())
        if product in products:
            raise ValueError(f"{context}; duplicate product normalization collision: {product!r}")

        products[product] = (
            _sku(row["sku (front)"], f"{context} field=sku (front)"),
            _sku(row["sku (back)"], f"{context} field=sku (back)"),
        )
    return ProductCatalog(products)


def _validate_product_locations(
    catalog: ProductCatalog, location_map: LocationMap, sources: Mapping[str, str],
) -> None:
    for product, skus in catalog.products.items():
        for sku in skus:
            if sku not in location_map:
                raise ValueError(
                    f"{sources['translation']}; product={product!r} SKU={sku!r} "
                    f"has no location in {sources['location_map']}"
                )


def _read_locations(path: Path, source: str) -> tuple[dict[str, Location], dict[str, str]]:
    locations: dict[str, Location] = {}
    zones: dict[str, str] = {}
    addresses: set[Location] = set()

    for line, row in _read_csv(path, source, ("zone", "aisle", "rack", "level", "sku")):
        context = f"{source}; CSV line={line}"
        zones.setdefault(row["zone"].strip().casefold(), row["zone"])
        location = _location(row, zones, context)
        sku = _sku(row["sku"], f"{context} field=sku")
        if sku in locations:
            raise ValueError(f"{context}; duplicate SKU mapping: {sku!r}")
        if location in addresses:
            raise ValueError(
                f"{context}; location normalization collision: "
                f"{_diagnostic_text(location, conversion='r')}"
            )

        locations[sku] = location
        addresses.add(location)

    return locations, zones


def _read_stock(
    path: Path, source: str, location_map: Mapping[str, Location], zones: Mapping[str, str],
) -> dict[Location, int]:
    canonical = {location: location for location in location_map.values()}
    stock: dict[Location, int] = {}

    for line, row in _read_csv(path, source, ("zone", "aisle", "rack", "level", "stock")):
        context = f"{source}; CSV line={line}"
        location = _location(row, zones, context)
        if location not in canonical:
            raise ValueError(
                f"{context}; unknown initial-stock location="
                f"{_diagnostic_text(location, conversion='r')}"
            )
        if location in stock:
            raise ValueError(
                f"{context}; duplicate initial-stock override location="
                f"{_diagnostic_text(location, conversion='r')}"
            )

        stock[canonical[location]] = _csv_integer(row["stock"], f"{context} field=stock")

    return stock


def _read_csv(
    path: Path, source: str, headers: tuple[str, ...],
) -> tuple[tuple[int, dict[str, str]], ...]:
    logger.debug(
        "function=config_loader._read_csv action=entry headers=%r",
        headers, extra={"source": source, "path": path},
    )
    reader = None
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.reader(stream, strict=True)
            raw_rows = [(reader.line_num, row) for row in reader]
    except UnicodeError as error:
        raise ValueError(f"{source}; CSV invalid UTF-8: {error}") from error
    except csv.Error as error:
        raise ValueError(f"{source}; CSV line={reader.line_num}: {error}") from error
    except OSError as error:
        raise OSError(f"{source}; cannot read CSV: {error}") from error

    if not raw_rows:
        raise ValueError(f"{source}; CSV line=1 missing header")

    normalized = _csv_headers(raw_rows[0][1], headers, source)
    return _csv_rows(raw_rows[1:], normalized, source)


def _csv_headers(
    supplied: list[str], expected: tuple[str, ...], source: str,
) -> tuple[str, ...]:
    normalized = tuple(value.strip().casefold() for value in supplied)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{source}; CSV line=1 header normalization collision: {normalized!r}")
    if set(normalized) != set(expected):
        raise ValueError(f"{source}; CSV line=1 expected headers={expected!r}, supplied={normalized!r}")
    return normalized


def _csv_rows(
    raw_rows: list[tuple[int, list[str]]], headers: tuple[str, ...], source: str,
) -> tuple[tuple[int, dict[str, str]], ...]:
    rows: list[tuple[int, dict[str, str]]] = []
    for line, row in raw_rows:
        if len(row) != len(headers):
            raise ValueError(
                f"{source}; CSV line={line} expected {len(headers)} fields, supplied={row!r}"
            )

        rows.append((line, dict(zip(headers, row, strict=True))))

    return tuple(rows)


def _location(row: Mapping[str, str], zones: Mapping[str, str], source: str) -> Location:
    zone_key = row["zone"].strip().casefold()
    if zone_key not in zones:
        raise ValueError(f"{source}; unknown location zone={row['zone']!r}")

    coordinates = (
        _csv_integer(row[field], f"{source} field={field}")
        for field in ("aisle", "rack", "level")
    )
    return Location(zones[zone_key], *coordinates)


def _sku(token: str, source: str) -> str:
    if not token.isdecimal() or not any(decimal(digit) for digit in token):
        raise ValueError(f"{source}; SKU must be a positive decimal string: {token!r}")
    return token


def _csv_integer(token: str, source: str) -> int:
    try:
        value = _decimal_integer(token, source)
    except ValueError as error:
        if getattr(error, "warehouse_logging_failure", False):
            raise
        raise ValueError(f"{source}; expected nonnegative integer, supplied={token!r}: {error}") from error
    if value < 0:
        raise ValueError(f"{source}; expected nonnegative integer, supplied={token!r}")
    return value


def _decimal_integer(token: str, source: str) -> int:
    """Preserve int's decimal spelling rules while converting bounded chunks."""
    stripped = token.strip()
    if re.fullmatch(r"[+-]?\d(?:_?\d)*", stripped) is None:
        raise ValueError("expected decimal integer")

    # int accepts Unicode whitespace, but not every character accepted by strip.
    # Validate the original surrounding whitespace using a single numeric digit.
    int(token.replace(stripped, "0", 1))
    digits = stripped.removeprefix("+").removeprefix("-").replace("_", "")

    value = 0
    for start in range(0, len(digits), 9):
        chunk = digits[start:start + 9]
        value = value * 10 ** len(chunk) + int(chunk)

    if stripped.startswith("-"):
        value = -value
    return value


def _employees(values: Mapping[str, object], source: str) -> dict[WorkerType, tuple[str, ...]]:
    aliases = {f"{role.value}s": role.value for role in WorkerType}
    employees: dict[WorkerType, tuple[str, ...]] = {}

    for supplied, names in values.items():
        normalized = supplied.strip().casefold()
        canonical = aliases.get(normalized, normalized)
        context = f"{source}; JSON field employees.{supplied}"
        role = _worker_role(canonical, supplied, context)
        if role in employees:
            raise ValueError(f"{context}; duplicate canonical/alias role={role.value!r}")

        employees[role] = _employee_names(names, context)
        if normalized in aliases:
            logger.warning(
                "function=config_loader._employees action=normalized "
                "legacy plural role=%r canonical=%s", supplied, role,
                extra={"source": source, "worker_type": role},
            )

    missing = tuple(role.value for role in WorkerType if role not in employees)
    if missing:
        raise ValueError(f"{source}; JSON field employees missing required roles={missing!r}")
    return employees


def _worker_role(canonical: str, supplied: str, context: str) -> WorkerType:
    try:
        return WorkerType(canonical)
    except ValueError as error:
        raise ValueError(
            f"{context}; unknown worker role={_diagnostic_text(supplied, conversion='r')}"
        ) from error


def _employee_names(names: object, context: str) -> tuple[str, ...]:
    if (
        not isinstance(names, list)
        or not names
        or not all(isinstance(name, str) for name in names)
    ):
        raise ValueError(
            f"{context}; require a nonempty array of employee names; "
            f"value={_diagnostic_text(names, conversion='r')}"
        )

    keys = [name.strip().casefold() for name in names]
    if len(set(keys)) != len(keys):
        raise ValueError(
            f"{context}; employee-name normalization collision: "
            f"{_diagnostic_text(names, conversion='r')}"
        )

    return tuple(names)


def _settings(
    warehouse: Mapping[str, object], identifiers: Mapping[str, object], source: str,
) -> dict[str, int | str]:
    settings: dict[str, int | str] = {}
    for section, values, names in (
        ("warehouse", warehouse, (
            "default_stock", "replenishment_threshold", "orders_per_request",
            "pallet_capacity_fascia", "truck_capacity_requests",
        )),
        ("identifiers", identifiers, (
            "first_order_number", "order_minimum_digits",
            "first_request_number", "request_minimum_digits",
            "first_truck_number", "truck_minimum_digits",
        )),
    ):
        for name in names:
            context = f"{source}; JSON field {section}.{name}"
            settings[name] = _numeric_setting(values, name, context)

    _validate_replenishment_threshold(settings, source)
    settings["request_timestamp_format"] = _request_timestamp_format(identifiers, source)
    return settings


def _numeric_setting(values: Mapping[str, object], name: str, context: str) -> int:
    value = _required(values, name, context)
    minimum = 0 if name == "replenishment_threshold" else 1
    if type(value) is not int or value < minimum:
        required_range = (
            "integer; require 0 <= replenishment_threshold < default_stock"
            if name == "replenishment_threshold" else "integer >= 1"
        )
        raise ValueError(
            f"{context}; expected {required_range}, "
            f"supplied={_diagnostic_text(value, conversion='r')}"
        )

    return value


def _validate_replenishment_threshold(settings: Mapping[str, int | str], source: str) -> None:
    if settings["replenishment_threshold"] >= settings["default_stock"]:
        raise ValueError(
            f"{source}; JSON field warehouse.replenishment_threshold; require "
            "0 <= replenishment_threshold < default_stock; "
            f"supplied={_diagnostic_text(settings['replenishment_threshold'], conversion='r')} "
            f"default_stock={_diagnostic_text(settings['default_stock'], conversion='r')}"
        )


def _request_timestamp_format(identifiers: Mapping[str, object], source: str) -> str:
    timestamp = _required(identifiers, "request_timestamp_format", f"{source}; JSON field identifiers")
    if timestamp != "%Y%m%d%H%M":
        raise ValueError(
            f"{source}; JSON field identifiers.request_timestamp_format must be "
            f"'%Y%m%d%H%M'; supplied={_diagnostic_text(timestamp, conversion='r')}"
        )
    return timestamp


def _section(document: Mapping[str, object], name: str, source: str) -> dict[str, object]:
    section = _required(document, name, source)
    if not isinstance(section, dict):
        raise ValueError(
            f"{source}; JSON field {name} must be an object; "
            f"value={_diagnostic_text(section, conversion='r')}"
        )
    return section


def _required(values: Mapping[str, object], field: str, source: str) -> object:
    if field not in values:
        raise ValueError(f"{source}; missing required JSON field {field}")
    return values[field]
