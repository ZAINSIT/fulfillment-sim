"""Inject the five required negative scenarios on top of the clean txn tables.

Outputs:
  data/txn/anomalous/{orders,order_lines,shipments,delivery_promises,tracking_events}.csv
  eval/ground_truth.csv

Every injected row is labelled in ground_truth.csv with:
  table, primary_key, issue_type, expected

`expected` is the correct label a detector should produce: for anomaly rows,
the detector should flag them with the same issue_type. Unlabelled rows must
pass all invariants — that is proven by scripts/check_anomalies.py.

This module never touches data/txn/*.csv. Run it after generate_txn.py.
"""

from __future__ import annotations
import pathlib
import pandas as pd

REPO = pathlib.Path(__file__).resolve().parent.parent
TXN = REPO / "data" / "txn"
OUT = REPO / "data" / "txn" / "anomalous"
EVAL = REPO / "eval"

def _load(name: str) -> pd.DataFrame:
    return pd.read_csv(TXN / name)

def _write(df: pd.DataFrame, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / name, index=False)

def main() -> None:
    orders = _load("orders.csv")
    lines = _load("order_lines.csv")
    shipments = _load("shipments.csv")
    promises = _load("delivery_promises.csv")
    tracking = _load("tracking_events.csv")

    gt_rows: list[dict] = []

    # ------------------------------------------------------------------
    # A1: promise_before_order — PROM.promised_date < order_ts.date()
    # Pick 5 orders deterministically (first 5 by order_id), rewrite
    # their promise date to the day before their order_ts.
    # ------------------------------------------------------------------
    target_orders = orders.sort_values("order_id").head(5)
    order_ts_by_id = dict(zip(orders["order_id"], pd.to_datetime(orders["order_ts"])))
    for oid in target_orders["order_id"]:
        mask = promises["order_id"] == oid
        bad_date = (order_ts_by_id[oid] - pd.Timedelta(days=1)).date().isoformat()
        promises.loc[mask, "promised_date"] = bad_date
        pid = promises.loc[mask, "promise_id"].iloc[0]
        gt_rows.append(dict(table="delivery_promises", primary_key=pid,
                            issue_type="promise_before_order", expected="flag"))

    # ------------------------------------------------------------------
    # A2: allocation_exceeds_stock — shipment qty > on_hand at node
    # Pick 5 shipments whose SKU has inventory at that node, set qty to
    # on_hand + 50.
    # ------------------------------------------------------------------
    inv = pd.read_csv(REPO / "data" / "master" / "inventory.csv")
    on_hand = {(r["node_id"], r["sku_id"]): int(r["on_hand"]) for _, r in inv.iterrows()}
    target = shipments[shipments["dispatch_ts"].notna()].sort_values("shipment_id").head(5)
    for sid in target["shipment_id"]:
        row = shipments.loc[shipments["shipment_id"] == sid].iloc[0]
        key = (row["node_id"], row["sku_id"])
        if key not in on_hand:
            continue
        shipments.loc[shipments["shipment_id"] == sid, "qty"] = on_hand[key] + 50
        gt_rows.append(dict(table="shipments", primary_key=sid,
                            issue_type="allocation_exceeds_stock", expected="flag"))

    # ------------------------------------------------------------------
    # A3: carrier_zone_unsupported — shipment ship_to_zone not in
    # carrier.service_zones. Rewrite the order's ship_to_zone to Z8 for
    # 5 distinct orders whose carrier does not support Z8.
    # Dedupe by order_id: an order with multiple shipments would otherwise
    # be labelled more than once, collapsing ground truth via set().
    # ------------------------------------------------------------------
    carriers = pd.read_csv(REPO / "data" / "master" / "carrier.csv")
    zone_by_carrier = dict(zip(carriers["carrier_id"],
                                carriers["service_zones"].fillna("")))
    target = shipments[shipments["dispatch_ts"].notna()].sort_values("shipment_id").head(400)
    injected = 0
    seen_orders: set[str] = set()
    for sid in target["shipment_id"]:
        if injected >= 5:
            break
        row = shipments.loc[shipments["shipment_id"] == sid].iloc[0]
        if row["order_id"] in seen_orders:
            continue
        zones = [z.strip() for z in str(zone_by_carrier.get(row["carrier_id"], "")).split(",") if z.strip()]
        if "Z8" in zones:
            continue
        orders.loc[orders["order_id"] == row["order_id"], "ship_to_zone"] = "Z8"
        seen_orders.add(row["order_id"])
        gt_rows.append(dict(table="orders", primary_key=row["order_id"],
                            issue_type="carrier_zone_unsupported", expected="flag"))
        injected += 1
    # ------------------------------------------------------------------
    # A4: delivered_before_dispatch — delivered_ts < dispatch_ts
    # Pick 5 shipments with non-null dispatch, set delivered_ts to
    # dispatch_ts - 2h.
    # ------------------------------------------------------------------
    target = shipments[shipments["delivered_ts"].notna()].sort_values("shipment_id").head(5)
    for sid in target["shipment_id"]:
        row = shipments.loc[shipments["shipment_id"] == sid].iloc[0]
        disp = pd.to_datetime(row["dispatch_ts"])
        shipments.loc[shipments["shipment_id"] == sid, "delivered_ts"] = (
            (disp - pd.Timedelta(hours=2)).isoformat()
        )
        gt_rows.append(dict(table="shipments", primary_key=sid,
                            issue_type="delivered_before_dispatch", expected="flag"))

    # ------------------------------------------------------------------
    # A5: split_status_not_rolled_up — order state != rollup of shipments
    # Pick 5 orders that are currently 'delivered' and set the order's
    # lifecycle_state to 'shipped', violating the rollup rule.
    # ------------------------------------------------------------------
    delivered_orders = orders[orders["lifecycle_state"] == "delivered"].sort_values("order_id").head(5)
    seen_a5: set[str] = set()
    for oid in delivered_orders["order_id"]:
        if oid in seen_a5:
            continue
        seen_a5.add(oid)
        orders.loc[orders["order_id"] == oid, "lifecycle_state"] = "shipped"
        gt_rows.append(dict(table="orders", primary_key=oid,
                            issue_type="split_status_not_rolled_up", expected="flag"))

    _write(orders, "orders.csv")
    _write(lines, "order_lines.csv")
    _write(shipments, "shipments.csv")
    _write(promises, "delivery_promises.csv")
    _write(tracking, "tracking_events.csv")

    EVAL.mkdir(parents=True, exist_ok=True)
    gt = pd.DataFrame(gt_rows)
    gt.to_csv(EVAL / "ground_truth.csv", index=False)
    counts = gt["issue_type"].value_counts().to_dict()
    print(f"wrote eval/ground_truth.csv ({len(gt)} rows)")
    for k, v in counts.items():
        print(f"  {k:28s} {v}")
    print(f"\nwrote data/txn/anomalous/*.csv")

if __name__ == "__main__":
    main()