"""Group canonical warehouse objects, shared enums and composed state.

Orders and requests retain their identities across workflow handoffs. Domain
objects expose business data without opening files or installing log handlers.

Useful entry traces, rejection evidence and committed lifecycle details use
DEBUG under --debug T; nested records wait for the owning
workflow commit. Domain objects store no console verbosity. WARNING/ERROR
retain problem context, while only ActivityLog.record_command emits INFO.
"""

# TODO: Preserve canonical object references throughout the fulfillment lifecycle.
