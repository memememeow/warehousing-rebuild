"""Define the Phase 1 warehouse simulator package.

The simulator coordinates sequential order fulfillment through explicit domain,
workflow, configuration and file boundaries. This scaffold declares contracts;
runtime behavior remains deferred.

Only ActivityLog.record_command emits INFO, as the exact log.txt command text
after an [INFO] prefix. Selected entry traces and useful lifecycle/startup
events belong at DEBUG. The launcher enables them with --debug T and suppresses
them with --debug F or omission; WARNING/ERROR remain visible in both modes.
"""

# TODO: Keep package imports free of startup, logging and simulation side effects.
