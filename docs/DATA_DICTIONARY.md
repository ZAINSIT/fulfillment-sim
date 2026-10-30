# Data Dictionary — Fulfillment Simulation

All timestamps are ISO-8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`). All money is INR, integer paise.
All IDs are immutable strings. Counters are zero-padded to 6 digits unless noted.

## 1. Order
| Field | Type | Req | Allowed / Units | Uniqueness | Validation |
|---|---|---|---|---|---|
| order_id | str | Y | `ORD-2026-\d{6}` | PK | regex + monotonic |
| customer_id | str | Y | `CUS-\d{6}` | — | exists in customer master |
| order_ts | datetime | Y | UTC | — | ≥ 2026-01-01, ≤ now |
| channel | enum | Y | `web,app,store,phone` | — | in enum |
| ship_to_zone | enum | Y | `Z1..Z8` | — | in enum |
| requested_service | enum | Y | `standard,express,same_day` | — | in enum |
| order_total_paise | int | Y | ≥ 0 | — | = Σ OrderLine.line_total_paise |
| lifecycle_state | enum | Y | `created,allocated,shipped,delivered,cancelled,exception` | — | transition rules in §12 |
| created_at | datetime | Y | UTC | — | = order_ts |

Cardinality: Order 1:N OrderLine; Order 1:N Shipment (split possible); Order 1:N DeliveryPromise (revisioned).

## 2. OrderLine
| Field | Type | Req | Allowed / Units | Uniqueness | Validation |
|---|---|---|---|---|---|
| order_line_id | str | Y | `OL-<order_id>-\d{2}` | PK | unique per order |
| order_id | str | Y | — | FK→Order | exists |
| sku_id | str | Y | `SKU-\d{6}` | FK→SKU | exists, active on order_ts |
| qty | int | Y | ≥ 1 | — | ≤ 99 |
| unit_price_paise | int | Y | ≥ 0 | — | = SKU.list_price_paise at order_ts unless promo |
| line_total_paise | int | Y | ≥ 0 | — | = qty * unit_price_paise |

## 3. SKU
| Field | Type | Req | Allowed / Units | Uniqueness | Validation |
|---|---|---|---|---|---|
| sku_id | str | Y | `SKU-\d{6}` | PK | regex |
| name | str | Y | ≤ 120 chars | — | non-empty |
| category | enum | Y | `apparel,electronics,grocery,home,beauty` | — | in enum |
| list_price_paise | int | Y | ≥ 0 | — | > 0 |
| weight_g | int | Y | ≥ 1 | — | ≤ 50000 |
| hazmat | bool | Y | — | — | if true, carrier must support hazmat |
| active_from | date | Y | — | — | ≤ active_to |
| active_to | date | N | null = open | — | ≥ active_from |

## 4. FulfillmentNode
| Field | Type | Req | Allowed / Units | Uniqueness | Validation |
|---|---|---|---|---|---|
| node_id | str | Y | `FUL-2026-\d{6}` | PK | regex |
| node_type | enum | Y | `warehouse,darkstore,store` | — | in enum |
| zone | enum | Y | `Z1..Z8` | — | in enum |
| daily_capacity_units | int | Y | ≥ 1 | — | > 0 |
| cutoff_hour_local | int | Y | 0–23 | — | valid hour |
| timezone | str | Y | IANA tz | — | valid tz |
| active | bool | Y | — | — | — |

## 5. Inventory
| Field | Type | Req | Allowed / Units | Uniqueness | Validation |
|---|---|---|---|---|---|
| inventory_id | str | Y | `INV-<node_id>-<sku_id>` | PK | (node,sku) unique |
| node_id | str | Y | — | FK→FulfillmentNode | exists |
| sku_id | str | Y | — | FK→SKU | exists |
| on_hand | int | Y | ≥ 0 | — | ≥ reserved |
| reserved | int | Y | ≥ 0 | — | ≤ on_hand |
| safety_stock | int | Y | ≥ 0 | — | ≤ on_hand |
| as_of_ts | datetime | Y | UTC | — | — |

Available to promise = on_hand − reserved − safety_stock. Never negative in allocation.

## 6. WarehouseCapacity
| Field | Type | Req | Allowed / Units | Uniqueness | Validation |
|---|---|---|---|---|---|
| capacity_id | str | Y | `CAP-<node_id>-<YYYY-MM-DD>` | PK | (node,date) unique |
| node_id | str | Y | — | FK→FulfillmentNode | exists |
| date | date | Y | — | — | within order horizon |
| units_capacity | int | Y | ≥ 1 | — | = node.daily_capacity_units unless holiday override |
| units_used | int | Y | ≥ 0 | — | ≤ units_capacity |
| is_holiday | bool | Y | — | — | if true, capacity = 0 |

## 7. Carrier
| Field | Type | Req | Allowed / Units | Uniqueness | Validation |
|---|---|---|---|---|---|
| carrier_id | str | Y | `CAR-\d{3}` | PK | regex |
| name | str | Y | — | — | non-empty |
| supports_hazmat | bool | Y | — | — | — |
| service_zones | str[] | Y | subset of `Z1..Z8` | — | non-empty |
| service_levels | str[] | Y | subset of `standard,express,same_day` | — | non-empty |
| cutoff_hour_local | int | Y | 0–23 | — | — |
| timezone | str | Y | IANA tz | — | — |

## 8. ServiceLevel
| Field | Type | Req | Allowed / Units | Uniqueness | Validation |
|---|---|---|---|---|---|
| service_level_id | str | Y | `SL-<name>` | PK | one per enum value |
| name | enum | Y | `standard,express,same_day` | — | — |
| transit_days_min | int | Y | ≥ 0 | — | ≤ transit_days_max |
| transit_days_max | int | Y | ≥ 0 | — | — |
| cutoff_required | bool | Y | — | — | if true, promise date depends on cutoff |

## 9. Shipment
| Field | Type | Req | Allowed / Units | Uniqueness | Validation |
|---|---|---|---|---|---|
| shipment_id | str | Y | `SHP-2026-\d{6}` | PK | regex |
| order_id | str | Y | — | FK→Order | exists |
| node_id | str | Y | — | FK→FulfillmentNode | exists |
| carrier_id | str | Y | — | FK→Carrier | exists, supports zone+level |
| service_level | enum | Y | `standard,express,same_day` | — | — |
| dispatch_ts | datetime | N | UTC | — | ≥ order_ts |
| delivered_ts | datetime | N | UTC | — | ≥ dispatch_ts; null if in transit |
| status | enum | Y | `planned,dispatched,in_transit,delivered,exception` | — | §12 |
| is_split | bool | Y | — | — | true if order has >1 shipment |
| parent_shipment_id | str | N | — | FK→Shipment | null if not split |

## 10. TrackingEvent
| Field | Type | Req | Allowed / Units | Uniqueness | Validation |
|---|---|---|---|---|---|
| tracking_id | str | Y | `TRK-<shipment_id>-\d{3}` | PK | unique per shipment |
| shipment_id | str | Y | — | FK→Shipment | exists |
| event_ts | datetime | Y | UTC | — | monotonic per shipment |
| event_type | enum | Y | `picked,departed,arrived,out_for_delivery,delivered,exception` | — | §12 |
| location_zone | enum | Y | `Z1..Z8` | — | — |
| note | str | N | ≤ 200 | — | — |

## 11. DeliveryPromise
| Field | Type | Req | Allowed / Units | Uniqueness | Validation |
|---|---|---|---|---|---|
| promise_id | str | Y | `PROM-<order_id>-\d{2}` | PK | revision suffix |
| order_id | str | Y | — | FK→Order | exists |
| promised_date | date | Y | — | — | ≥ order_ts.date() unless anomaly flagged |
| promised_service | enum | Y | `standard,express,same_day` | — | — |
| node_id | str | Y | — | FK→FulfillmentNode | the node that won the promise |
| reason_code | enum | Y | `single_node,split_node,capacity_shift,carrier_override,fallback` | — | traceability |
| created_at | datetime | Y | UTC | — | — |
| supersedes_promise_id | str | N | — | FK→DeliveryPromise | revision chain |

## 12. Lifecycle transition rules
- Order: created → allocated → shipped → delivered; any → cancelled before dispatch; any → exception on anomaly.
- Shipment: planned → dispatched → in_transit → delivered; any → exception.
- TrackingEvent: must be monotonic; `delivered` only once; `delivered_ts` = last `delivered` event.
- Promise: revisions append, never overwrite; latest revision is authoritative.

## 13. Cross-entity invariants (enforced in `src/validate.py`)
1. `Order.order_total_paise = Σ OrderLine.line_total_paise`.
2. `Σ Shipment qty per OrderLine ≤ OrderLine.qty` at allocation time.
3. Allocation at any node ≤ `available_to_promise` at decision time.
4. Allocation per (node, date) ≤ `WarehouseCapacity.units_capacity` unless `is_holiday` (then 0).
5. `Shipment.promised_date ≥ order_ts.date()` unless row flagged in `ground_truth.csv`.
6. `Carrier.service_zones` ⊇ ship_to_zone for every shipment.
7. `Carrier.service_levels` ⊇ service_level for every shipment.
8. `dispatch_ts ≥ order_ts`; `delivered_ts ≥ dispatch_ts` unless anomaly flagged.
9. TrackingEvent timestamps monotonic per shipment.
10. If order has multiple shipments, `Order.lifecycle_state` = rollup of shipment statuses (delivered only if all delivered).
11. Every FK exists in its parent table.
12. Every PK unique.

## 14. Required labelled anomalies (Step 5)
Injected deliberately, each with `issue_type` and `ground_truth = true`:
- `promise_before_order` — PROM.promised_date < order_ts.date()
- `allocation_exceeds_stock` — shipment qty > on_hand at node
- `carrier_zone_unsupported` — shipment zone ∉ carrier.service_zones
- `delivered_before_dispatch` — delivered_ts < dispatch_ts
- `split_status_not_rolled_up` — order state ≠ rollup of shipment states
All other rows must be valid. Ground truth is written to `eval/ground_truth.csv` and never read by the decision engine.