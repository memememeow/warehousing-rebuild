"""Group startup validation and immutable configuration evidence.

Configuration loading owns input normalization and reference validation.
Published settings are shared with the composition root and warehouse owners.

Configuration details use DEBUG, legacy aliases use WARNING and fatal input
problems use ERROR. Only ActivityLog.record_command emits INFO. The launcher
selects verbosity before preflight; debug mode adds no configuration fields.
"""

# TODO: Keep configuration publication separate from mutable workflow state.
