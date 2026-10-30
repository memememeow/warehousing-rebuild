"""Declare validation of JSON configuration and its three CSV references.

Preflight owns normalized-key collision detection, catalog and location
coverage, stock constraints, worker roles and identifier settings. Validation
must preserve display spelling and report source context before state exists.
"""

from __future__ import annotations

from pathlib import Path

from warehouse.configuration.warehouse_config import WarehouseConfig

# TODO: Validate all inputs, resolve references and warn about legacy settings.


class ConfigLoader:
    """Publish one immutable configuration after complete reference validation."""

    def load_config(self, config_path: Path) -> WarehouseConfig:
        """Validate JSON and referenced CSVs; raise ValueError or OSError on failure."""
        ...
