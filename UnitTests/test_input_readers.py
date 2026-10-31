"""Reserve coverage for configuration preflight and order grammar validation.

Future cases cover launch-relative and config-relative references, absolute
inputs, missing files, malformed JSON and CSVs, mixed case, normalization
collisions, SKU coverage, employee roles and preserved order source context.

Planned launcher cases cover omitted --debug (default F), explicit T/F, lowercase
and other invalid values, missing values and help documenting the default.
Argument rejection and help must precede file reads, state construction and
run-folder creation. Debug verbosity adds no WarehouseConfig field.
"""

# TODO: Add deterministic reader fixtures and rejection cases with source context.
