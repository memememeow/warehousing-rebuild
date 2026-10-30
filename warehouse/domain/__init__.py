"""Group canonical warehouse objects, shared enums and composed state.

Orders and requests retain their identities across workflow handoffs. Domain
objects expose business data without opening files or installing log handlers.
"""

# TODO: Preserve canonical object references throughout the fulfillment lifecycle.
