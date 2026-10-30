"""Group read-only route planning independently of workflow mutation.

Picking supplies location occurrences and retains responsibility for matching
the returned route to required SKUs before changing queue membership.
"""

# TODO: Preserve the planner's location-only contract as routing evolves.
