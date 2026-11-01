# Phase 1 verification plan scaffold

This file reserves the architecture's test plan. Every row describes planned
coverage; step 01 CLI boundary tests are in `test_input_readers.py`, and step 02
console diagnostics and failure cases are in `test_outputs.py`. Other requirements
remain unverified until their implementation and relevant tests pass.

Use standard-library unittest, deterministic fixtures, temporary directories and
mocked clock or I/O boundaries. Initialize numeric settings once per test process;
run alternate configurations and timezone cases in separate processes.

| Requirement | Owner | Planned fixture | Expected result | Level |
| --- | --- | --- | --- | --- |
| State composition | WarehouseState | Validated startup snapshot | Five owners share canonical objects without duplicate collections | Unit |
| Immutable inputs | WarehouseConfig, ProductCatalog, RoutePlanner | Mutable source mappings changed after construction | Defensive frozen mappings preserve their original contents | Unit |
| CLI preflight | Launcher | Help, missing arguments and complete valid inputs | Help creates no state or output; invalid startup returns nonzero | Integration |
| Reference resolution | Launcher, ConfigLoader | Relative and absolute inputs from different launch directories | CLI references use launch context; CSV references use config context; working directory stays unchanged | Integration |
| Missing and malformed inputs | ConfigLoader, OrderReader | Missing files, invalid UTF-8, malformed JSON, bad CSV headers and rows | Fatal preflight rejection with source context before state construction | Unit |
| Case normalization | ConfigLoader, ProductCatalog, OrderReader | Mixed-case keys, headers, products, roles, names and zones | Equivalent text resolves consistently while display spelling and filename case survive | Unit |
| Normalization collisions | ConfigLoader, ProductCatalog | Duplicate JSON keys, headers, product pairs, locations and same-role names | Ambiguous inputs are rejected before dictionary publication | Unit |
| Catalog and stock constraints | ConfigLoader | Missing SKU coverage, unknown stock locations, invalid SKU strings and negative coordinates | Invalid reference data is rejected with observed values and source context | Unit |
| Numeric initialization | Launcher and constant owners | Valid defaults, booleans, fractions, invalid ranges and repeated initialization | Settings initialize once after validation; early reads and repeated initialization fail | Integration |
| Legacy settings | ConfigLoader | Plural roles, duplicate aliases and obsolete output or timezone fields | Supported aliases warn; duplicate roles fail; obsolete fields warn and are ignored | Unit |
| Order grammar | OrderReader | Blank lines, keyword variants, duplicate products and wrong field counts | Grammar is checked in full; valid order spelling, order and source lines are retained | Unit |
| Order acceptance | OrderRegistry | Unknown products, missing locations and valid duplicate lines | Rejected orders consume no IDs; accepted duplicates remain distinct orders | Unit |
| Batch boundaries | OrderRegistry | Zero, one, three, four and five accepted orders | Oldest full batches and one final partial batch form without an extra empty request | Unit |
| Input closure | OrderRegistry | Repeated closure and acceptance after closure | No duplicate final request; new orders are rejected without changing counters | Unit |
| Identifier allocation | OrderRegistry, LoadingWorkflow | Controlled aware times, large counters and truck replacement | Counters start at one, widths are minimums and numeric request sequence remains independent | Unit |
| Local time | OrderRegistry, OutputWriter | Naive datetimes, daylight-saving boundaries and repeated local minutes | Naive values fail; each aware instant uses its local offset; counters preserve uniqueness | Integration |
| Request lifecycle | FulfillmentRequest and active workflows | Every allowed transition plus illegal transitions | Only legal transitions commit; fixed request data remains read-only | Unit |
| Route coverage | RoutePlanner, PickingWorkflow | Duplicate locations, missing occurrences, invented locations and shared locations | Valid routes preserve every occurrence; invalid routes do not dequeue work | Unit |
| Pick validation | PickingWorkflow | Wrong worker, role, assignment, number and scanned SKU | Rejections preserve assignments, stock, progress and last committed status | Unit |
| Inventory and refill | Inventory | Default and overridden stock, zero-stock picks and threshold crossing | Unknown or empty stock rejects without mutation; threshold refill completes synchronously | Unit |
| Inventory snapshots | Inventory | All mapped locations and attempted snapshot mutation | Read-only copies include unchanged stock in traversal order | Unit |
| Marshalling | PickingWorkflow | Incomplete and complete pick progress | Only fully picked requests are released and assignments clear with status changes | Unit |
| Sequencing and rework | SequencingWorkflow, PickingWorkflow | FIFO requests, staged evidence, premature rescans and content failures | Wrong command stages preserve evidence; content failure returns the same request for fresh picks without stock credit | Unit |
| Ordered loading | LoadingWorkflow | Later-ready requests, invalid staging and invalid final rescans | Only the next numeric sequence loads; retries preserve assignment and committed status | Unit |
| Truck commit and capacity | Truck, LoadingWorkflow | Duplicate loads, overflow, five requests and one later request | Successful loads commit once; full trucks dispatch and replacement preserves global sequence | Unit |
| Deterministic completion | WarehouseApplication | Full batches, final partial batch, empty input and blocked work | First configured workers complete each batch; blocked runs report every request and return nonzero | Integration |
| Console diagnostics (step 02) | Launcher, ActivityLog | Omitted/F/T debug, shared module loggers, repeated setup, invalid feedback levels, write/flush/short-write failures | Thresholds and disjoint streams prevent duplicates; INFO feedback fails before output; transport errors propagate and launcher attempts direct stderr fallback | Unit / integration |
| Logging and trace grammar | ActivityLog | Every command variant with full contextual identifiers | Exact canonical command mirrors flush before trace append; diagnostics retain all order IDs | Unit |
| Logging failures | ActivityLog, launcher | Mirror, append and flush failures | Commands stop; mirror failure adds no trace line; last confirmed line is reported | Integration |
| Run-folder creation | OutputWriter | Existing names and noncollision directory errors | Exclusive creation retries only collisions and preserves earlier runs | Unit |
| Manifests and inventory | OutputWriter | Departed, partial and empty trucks plus complete map | One section per loaded truck; empty trucks omitted; every mapped location is exported | Unit |
| Publication failures | OutputWriter, launcher | Staging, publication and cleanup failures | Nonzero completion preserves trace, removes final artifacts where possible and reports retained incomplete artifacts | Integration |

TODO: Turn the planned fixtures into executable owner tests as behavior is
implemented. Scaffold syntax and import checks do not verify these requirements.
