"""Group read-only route planning independently of workflow mutation.

Picking supplies location occurrences and retains responsibility for matching
the returned route to required SKUs before changing queue membership.

Function entry/results and route details use DEBUG under --debug T, with no
planner verbosity property or INFO events. The launcher controls filtering.
"""

# TODO: Preserve the planner's location-only contract as routing evolves.
