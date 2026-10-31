"""Group order input, diagnostic transport and final-output boundaries.

These adapters own filesystem and console access. Workflow owners supply
canonical objects and snapshots after completed in-memory changes.

ActivityLog.record_command alone mirrors accepted commands at INFO with exact
log.txt text. Other function details use DEBUG; problems use WARNING/ERROR.
The launcher selects DEBUG for --debug T or INFO for F/omission and configures
stdout for enabled DEBUG/INFO and stderr for WARNING/ERROR without duplicates.
"""

# TODO: Keep file and console failures visible to the launcher.
