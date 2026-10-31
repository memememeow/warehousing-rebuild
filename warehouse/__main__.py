"""Bootstrap absolute imports for the executable warehouse directory.

The bootstrap makes absolute package imports available and passes command
arguments to the launcher without changing the process working directory.
It propagates the launcher's exit status.
Forward --debug T/F unchanged; argument validation and console thresholds belong
to the launcher. The bootstrap emits no INFO records.
"""

from __future__ import annotations

import sys
from pathlib import Path


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))

    from warehouse.launcher import warehouse

    raise SystemExit(warehouse(sys.argv[1:]))
