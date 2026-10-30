## D-01 — Holiday capacity is 0, not "reduced"
**Context:** Carrier and warehouse ops stop on public holidays. The problem
statement asks for capacity limits and holidays as edge cases.

**Decision:** `WarehouseCapacity.units_capacity = 0` and `units_used = 0` on
holiday dates. The pydantic `WarehouseCapacity` model enforces both, and
`src/decide.py` (next phase) will treat a zero-capacity day as a hard
allocation blocker, not a soft constraint.

**Alternatives rejected:** (a) modelling holiday capacity as a percentage
reduction adds a tunable knob with no ground truth; (b) omitting holiday rows
entirely hides the constraint from the validator and makes downstream joins
silently miss dates. Explicit zero is verifiable and keeps the date axis
complete for every node.

**Consequence:** `gen_capacity` writes 90 rows per node (one per day), always.
The validator can assert `is_holiday → units_capacity == 0` as a one-line check.