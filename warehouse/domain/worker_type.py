"""Define the canonical worker roles shared by configuration and workflows.

String values are singular and suitable for configuration keys and diagnostic
records. Legacy aliases belong to configuration validation rather than this
enum; employees continue to be identified by configured names.
"""

from __future__ import annotations

from enum import StrEnum

# TODO: Normalize legacy role aliases at the configuration boundary.


class WorkerType(StrEnum):
    """Identify the four supported employee responsibilities."""

    PICKER = "picker"
    SEQUENCER = "sequencer"
    LOADER = "loader"
    REPLENISHER = "replenisher"
