"""Declare validation of JSON configuration and its three CSV references.

Preflight owns normalized-key collision detection, catalog and location
coverage, stock constraints, worker roles and identifier settings. Validation
must preserve display spelling and report source context before state exists.

The launcher configures console verbosity before loading. Function entry/results,
paths, validation decisions and row counts use DEBUG under --debug T; legacy
normalization uses WARNING and fatal preflight problems use ERROR. Emit no INFO
and keep the launcher-only debug setting outside JSON and WarehouseConfig.
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
