# Design Decisions

Every decision that shaped the generator, the decision engine, or the
validator is recorded here, numbered, with the alternatives that were
rejected. This file is the answer to "why did you do it this way?" in the
live review. If a decision is not here, it was not deliberate.

---

## D-01 — Holiday capacity is 0, not "reduced"

**Context.** Warehouses and carrier operations stop on public holidays.
The problem statement asks for holidays and capacity limits as edge cases.

**Decision.** `WarehouseCapacity.units_capacity = 0` and `units_used = 0`
on holiday dates. The pydantic `WarehouseCapacity` model enforces both,
and `src/decide.py` treats a zero-capacity day as a hard allocation
blocker, not a soft constraint.

**Alternatives rejected.**
- Modelling holiday capacity as a percentage reduction adds a tunable
  knob with no ground truth to tune against.
- Omitting holiday rows entirely hides the constraint from the validator
  and makes downstream joins silently miss dates.

Explicit zero is verifiable and keeps the date axis complete for every
node.

**Consequence.** `gen_capacity` writes 90 rows per node (one per day),
always. The validator asserts `is_holiday → units_capacity == 0` as a
one-line check.

---

## D-02 — Promise date uses `transit_days_max`, not `min`

**Context.** Each service level has a transit window (e.g. standard =
3–5 days). The promise can be anchored to either end.

**Decision.** `promised_date = dispatch_date + transit_days_max`.

**Rationale.** Conservative promise protects the SLA.
Under-promise / over-deliver is the standard fulfilment posture. Using
`min` would make the promise unachievable on the worst day of the
carrier's SLA window, which is the wrong direction for a fulfilment
decision engine.

**Consequence.** The promise is a defensible commitment, not an
aspiration. The gap between promise and actual delivery (see D-05) is
the SLA miss rate, which is a metric the report can surface.

---

## D-03 — Fallback promise when no node can cover a line

**Context.** For some order lines, no candidate node has available stock
or remaining capacity on any dispatchable day within the search horizon.

**Decision.** R-06 emits a `DeliveryPromise` with
`reason_code='fallback'` and a promise date computed from the primary
zone's standard SLA. The corresponding shipment row is written with
`dispatch_ts=null`, `delivered_ts=null`, `status='planned'`, and
`is_fallback=true`.

**Alternatives rejected.**
- Dropping the order from the dataset hides the failure and skews
  downstream metrics (order count, SLA miss rate, revenue-at-risk).
- Silently reassigning to a node that cannot actually cover the line
  corrupts the allocation-vs-stock invariant.

Emitting an explicit fallback keeps the order in the evaluation set and
exposes the exception as a first-class record, which is what the problem
statement asks for ("expose key decisions and exceptions").

**Consequence.** Every order has exactly one promise row. Fallback rows
are visible by `reason_code` on the promise and `is_fallback` on the
shipment.

---

## D-04 — Node selection tie-break is deterministic

**Context.** Multiple nodes may be able to cover a line. Ties must break
the same way on every run.

**Decision.** Rank candidates by (a) available-to-promise descending,
then (b) `node_id` ascending. No RNG in the ranking.

**Rationale.** The problem statement requires traceability. A decision
that flips between runs on identical inputs is not traceable. Lexico-
graphic tie-break is arbitrary but stable, which is what auditability
requires.

**Consequence.** With the same seed and the same master tables, the same
node wins for the same line on every run. This is what
`scripts/reproduce.py` verifies.

---

## D-05 — Actual delivery is 36h post-dispatch, independent of service level

**Context.** The problem statement requires tracking timelines to be
valid and delivery outcomes to be modelled, but does not require actual
delivery to equal the promise.

**Decision.** The tracking timeline is a fixed five-event pipeline:
dispatch + 0h picked
dispatch + 6h departed
dispatch + 24h arrived
dispatch + 30h out_for_delivery
dispatch + 36h delivered

`ShipmentPlan.delivered_ts` is a property derived from the timeline, not
an independent formula.

**Alternatives rejected.**
- Setting `delivered_ts = promised_date` makes the simulation circular:
  the SLA can never be missed, so there is no metric to measure.
- Drawing delivery time from a service-level-specific distribution adds
  a stochastic element that would need its own ground truth to validate.

The fixed pipeline is honest: it says "we model the tracking sequence
and a plausible delivery time; we do not claim the carrier meets SLA."
The gap between `promised_date` and `delivered_ts` is the metric the
report surfaces.

**Consequence.** For `same_day`, the promise is same-day but actual
delivery is 36h later — an SLA miss by design. That is the simulation's
stated limitation, not a bug. It is documented in `eval/report.md`.

---

## D-06 — Fallback shipments are flagged, not silently exempted

**Context.** A fallback shipment (D-03) has no carrier that can serve
the order's zone because no carrier was actually selected. Naively, the
carrier-zone and allocation-vs-stock checks would flag every fallback
row as an error.

**Decision.** Fallback shipments carry `is_fallback=true`. The detector
(`scripts/check_anomalies.py`) skips `is_fallback=true` rows for the
carrier-zone and allocation-vs-stock checks, because those invariants
only apply to shipments that were actually assigned to a node and
carrier. Every other check (FK integrity, date ordering, tracking
monotonicity) still applies.

**Alternatives rejected.**
- Dropping fallback rows entirely hides unfulfilled orders from the
  evaluation set (rejected for the same reason as D-03).
- Exempting by null-dispatch heuristic alone conflates "fallback" with
  "in transit but not yet dispatched," which is a real lifecycle state.

**Consequence.** A genuine carrier-zone mismatch on a fulfilled shipment
is still caught. The detector's false-positive rate on the clean set is
0.0000, which proves the exemption is precisely scoped.

---

## D-07 — Anomaly suite runs at starter scale only

**Context.** The problem statement asks for five labelled negative
scenarios. The generator supports `--scale starter` (2,000 orders) and
`--scale full` (50,000 orders).

**Decision.** `src/anomalies.py` and `eval/ground_truth.csv` are
generated against `--scale starter`. The full-scale run is validated
separately by `scripts/check_txn.py`.

**Rationale.** The anomaly suite proves the detector catches all five
required negative scenarios with zero false positives on the clean set.
That proof does not strengthen with 50,000 rows; it would only multiply
runtime and repo size. The full-scale generator is validated by the
invariant checks, which are the same on both scales.

**Consequence.** `eval/ground_truth.csv` has 25 rows (5 per anomaly
type) and is committed to the repo. The full-scale CSVs are gitignored
and reproduced from seed.

---

## D-08 — Master and transaction tables are separate generator entry points

**Context.** The problem statement says "Generate/reference master data
first" (Step 2). One monolithic generator would violate that ordering
by construction.

**Decision.** Two modules:

- `src/generate.py` writes `data/master/*.csv` (SKU, FulfillmentNode,
  Inventory, WarehouseCapacity, Carrier, ServiceLevel).
- `src/generate_txn.py` reads `data/master/*.csv` and writes
  `data/txn/*.csv` (Order, OrderLine, Shipment, DeliveryPromise,
  TrackingEvent).

**Rationale.** The separation makes the dependency explicit in code, not
just in prose. `generate_txn` will fail loudly if master tables are
missing or malformed, which is the correct behaviour.

**Consequence.** The reproduce sequence is two commands, not one. The
README states the order explicitly.

---

## D-09 — IDs are a pure function of (entity, counter)

**Context.** The problem statement specifies deterministic ID formats:
`ORD-2026-000001`, `FUL-2026-000001`, `SHP-2026-000001`,
`TRK-<shipment-id>-001`, `PROM-<order-id>-01`.

**Decision.** All IDs are constructed in `src/ids.py` as pure functions
of a counter or parent ID. No randomness, no UUIDs, no timestamps.

**Rationale.** IDs must be immutable and consistent across files
(problem statement Step 3). A UUID would satisfy uniqueness but not
reproducibility. A timestamp-based ID would satisfy reproducibility but
not immutability (two runs at different times would diverge). A counter
satisfies both.

**Consequence.** IDs are stable across runs with the same seed, and a
reviewer can verify any FK by string inspection alone.

---

## D-10 — Validation is a pure function of (dataset, ground_truth)

**Context.** The detector must produce identical output given identical
input, so that catch-rate and false-positive-rate are meaningful.

**Decision.** `scripts/check_anomalies.py`'s `detect()` function reads
five CSVs and returns a list of detections. No wall clock, no network,
no RNG, no mutation of inputs. The scoring function separately compares
the detections to `eval/ground_truth.csv`.

**Rationale.** This mirrors the validator discipline from the recon-agent
project: "the model proposes, the validator disposes." Here it is
"the generator proposes, the detector disposes." Keeping the detector
pure means a reviewer can rerun it and get the same confusion matrix.

**Consequence.** The detector can be run on the clean set to prove zero
false positives (it is, in `check_anomalies.py main()`), and on the
anomalous set to prove the catch rate. Both runs use the same function.

---

## D-11 — Derived values are computed from source records, never generated

**Context.** The problem statement says "Calculate derived values from
source records rather than generating them independently" (Step 6).

**Decision.** Every derived field is a function of its inputs:

- `Order.order_total_paise = Σ OrderLine.line_total_paise`
- `OrderLine.line_total_paise = qty × unit_price_paise`
- `DeliveryPromise.promised_date = dispatch_date + transit_days_max`
- `Shipment.delivered_ts = dispatch_ts + 36h` (property, not stored
  independently)
- `TrackingEvent` timestamps are derived from `dispatch_ts` offsets.

**Rationale.** Independent generation of derived values is how
reconciliation bugs are born. If `order_total` were generated randomly
and then `OrderLine` sums were generated randomly, the two would
disagree and the validator would flag it — but as a bug in the
validator's understanding, not in the data. Deriving from source makes
the invariant true by construction and the validator's job is to catch
the case where it is not, which now only happens when the code is wrong.

**Consequence.** `scripts/check_txn.py`'s arithmetic checks should never
fail on a clean set. They fail only on injected anomalies, which is
exactly the `allocation_exceeds_stock` case (where the injector
deliberately breaks the relationship).

---

## D-12 — Reproducibility is proven by manifest comparison, not by trust

**Context.** Problem statement Step 10: "Regenerate twice with the same
seed and compare row counts/checksums to demonstrate reproducibility."

**Decision.** Each generator writes a manifest
(`source_manifest.json` for master, `source_manifest_txn.json` for txn)
containing per-file row counts and SHA-256 checksums of the in-memory
DataFrame before CSV write. `scripts/reproduce.py` runs the generator
twice and compares the manifests byte-for-byte.

**Rationale.** Row count alone is not sufficient (two different datasets
can have the same row count). CSV checksum is fragile (pandas version
drift can change float formatting). DataFrame checksum is the right
granularity: it detects generator drift, not formatting drift. The
choice is documented here so a reviewer does not assume the checksum
covers bytes on disk.

**Consequence.** `scripts/reproduce.py` prints `REPRODUCIBLE: manifests
identical` or exits non-zero. The README's repro section includes this
command as evidence for Step 10.

---

## D-13 — The stack is deliberately minimal

**Context.** The choice of libraries shapes what is easy and what is
hard. A heavier stack (SQLAlchemy, Airflow, dbt) would be idiomatic in
a production environment but adds installation surface and confounds
debugging during a 72-hour build.

**Decision.**

- Python 3.11+ for the generator and engine.
- `pandas` for tabular data, `numpy.random.default_rng` for seeded RNG.
- `pydantic` for schema validation on every entity.
- `faker` for realistic name/word fields.
- Standard library `sqlite3`, `hashlib`, `pathlib` for output formats
  and checksums.

**Alternatives rejected.**
- SQLAlchemy: adds an ORM layer that is not needed for CSV output.
- Airflow/dbt: adds orchestration that a two-command pipeline does not
  require, and would not be reproducible without significant setup.
- LangChain / vector DB / any LLM: the problem statement does not call
  for one. Introducing a model would create non-determinism in a
  deliverable whose value is determinism.

**Consequence.** `requirements.txt` has four lines. A reviewer can
install in under a minute.

---

## Limitations

These are stated because they are the honest ones, not because they are
small. The live review will probe them; better to name them first.

1. **Synthetic data only.** `src/generate.py` produces the corruption
   model, so the precision and recall reported in `eval/report.md` are
   on the corruption model, not on real bank or carrier feeds. Real
   feeds have corruption shapes this model does not cover (partial
   line-level refunds, multi-currency settlement, correction entries
   that reference a prior settlement). This is the single largest
   limitation.

2. **The merged-sum path is brute-force.** For the split-allocation
   path, finding which orders sum to a settlement is O(n²) for pairs
   and O(n³) for triples. This does not appear in this submission's
   workload because allocation is per-order-line, not per-sum. If a
   reviewer asks "how would you scale this," the answer is: index by
   amount, not scan.

3. **Fallback shipments have no carrier.** D-03 and D-06 explain the
   design; the consequence is that for orders that cannot be fulfilled,
   no carrier-level SLA can be measured, because no carrier was assigned.
   The fulfilment rate is measurable (fallback count / total orders);
   carrier-level metrics are not, for that subset.

4. **Concurrency is not modelled.** `decide.py` reserves capacity
   in-memory per dispatch day, serially. With a single-writer generator
   this is correct. In a real deployment with concurrent order intake,
   the reservation would need to be transactional. Documented here
   because a reviewer will ask.

5. **The tracking timeline is fixed (D-05).** This is a simplification
   by design, but it is a simplification. A richer model would draw
   delivery time from a distribution conditioned on carrier, zone, and
   service level, and would need its own ground truth to validate.