"""Detector: run every invariant check against the anomalous tables and
compare detections to eval/ground_truth.csv. Prints a confusion summary.

This is also the validator used for the clean set — the clean set should
produce zero detections. Same code, different input."""

from __future__ import annotations
import pathlib, sys
import pandas as pd

REPO = pathlib.Path(__file__).resolve().parent.parent
ANOM = REPO / "data" / "txn" / "anomalous"
GT = REPO / "eval" / "ground_truth.csv"
MASTER = REPO / "data" / "master"

def detect(base: pathlib.Path) -> pd.DataFrame:
    """Return a DataFrame of detections: table, primary_key, issue_type."""
    orders = pd.read_csv(base / "orders.csv")
    shipments = pd.read_csv(base / "shipments.csv")
    promises = pd.read_csv(base / "delivery_promises.csv")
    inv = pd.read_csv(MASTER / "inventory.csv")
    carriers = pd.read_csv(MASTER / "carrier.csv")

    dets: list[dict] = []

    # A1: promise before order
    o_ts = pd.to_datetime(orders.set_index("order_id")["order_ts"]).dt.date
    p_dt = pd.to_datetime(promises.set_index("order_id")["promised_date"]).dt.date
    bad = p_dt < o_ts.reindex(p_dt.index)
    for pid, oid in zip(promises["promise_id"], promises["order_id"]):
        if bad.get(oid, False):
            dets.append(dict(table="delivery_promises", primary_key=pid,
                             issue_type="promise_before_order"))

    # A2: allocation exceeds stock
    on_hand = {(r["node_id"], r["sku_id"]): int(r["on_hand"]) for _, r in inv.iterrows()}
    for _, row in shipments.iterrows():
        key = (row["node_id"], row["sku_id"])
        if key in on_hand and int(row["qty"]) > on_hand[key]:
            dets.append(dict(table="shipments", primary_key=row["shipment_id"],
                             issue_type="allocation_exceeds_stock"))

    # A3: carrier zone unsupported
    zones_by_carrier = dict(zip(carriers["carrier_id"], carriers["service_zones"].fillna("")))
    order_zone = dict(zip(orders["order_id"], orders["ship_to_zone"]))
    for _, row in shipments.iterrows():
        if bool(row.get("is_fallback", False)):
            continue                                  # R-06: no carrier assigned
        zones = zones_by_carrier.get(row["carrier_id"], "").split(",")
        zone = order_zone.get(row["order_id"])
        if zone and zones and zone not in zones:
            dets.append(dict(table="orders", primary_key=row["order_id"],
                             issue_type="carrier_zone_unsupported"))

    # A4: delivered before dispatch
    sh = shipments.copy()
    sh["dispatch_ts"] = pd.to_datetime(sh["dispatch_ts"], errors="coerce")
    sh["delivered_ts"] = pd.to_datetime(sh["delivered_ts"], errors="coerce")
    for _, row in sh.iterrows():
        if pd.notna(row["dispatch_ts"]) and pd.notna(row["delivered_ts"]) \
           and row["delivered_ts"] < row["dispatch_ts"]:
            dets.append(dict(table="shipments", primary_key=row["shipment_id"],
                             issue_type="delivered_before_dispatch"))

    # A5: split status not rolled up
    # Rollup rule: order is 'delivered' iff all shipments 'delivered'.
    by_order = shipments.groupby("order_id")["status"].apply(lambda s: set(s.unique()))
    for _, o in orders.iterrows():
        states = by_order.get(o["order_id"], set())
        all_delivered = states == {"delivered"}
        any_in_flight = states & {"planned", "dispatched", "in_transit"}
        if o["lifecycle_state"] == "delivered" and not all_delivered:
            dets.append(dict(table="orders", primary_key=o["order_id"],
                             issue_type="split_status_not_rolled_up"))
        elif o["lifecycle_state"] == "shipped" and all_delivered and states:
            dets.append(dict(table="orders", primary_key=o["order_id"],
                             issue_type="split_status_not_rolled_up"))

    return pd.DataFrame(dets)

def score(dets: pd.DataFrame, gt: pd.DataFrame, table_label: str) -> None:
    key_cols = ["table", "primary_key", "issue_type"]
    det_keys = set(map(tuple, dets[key_cols].values)) if len(dets) else set()
    gt_keys = set(map(tuple, gt[key_cols].values)) if len(gt) else set()

    tp = len(det_keys & gt_keys)
    fp = len(det_keys - gt_keys)
    fn = len(gt_keys - det_keys)
    total = len(gt_keys)
    print(f"\n=== {table_label} ===")
    print(f"ground_truth rows : {total}")
    print(f"detections        : {len(det_keys)}")
    print(f"true positive     : {tp}")
    print(f"false positive    : {fp}")
    print(f"missed (FN)       : {fn}")
    print(f"catch rate        : {tp/total:.4f}" if total else "catch rate        : n/a")
    print(f"FP rate           : {fp/max(len(det_keys),1):.4f}" if det_keys else "FP rate           : n/a")
    if fn:
        print("MISSED:")
        for k in sorted(gt_keys - det_keys):
            print("  -", k)
    if fp:
        print("FALSE POSITIVES:")
        for k in sorted(det_keys - gt_keys):
            print("  -", k)

def main() -> int:
    gt = pd.read_csv(GT)
    dets = detect(ANOM)
    score(dets, gt, "anomalous set")

    # sanity: same detector on the clean set must yield zero detections
    clean_dets = detect(REPO / "data" / "txn")
    if len(clean_dets):
        print(f"\nCLEAN SET has {len(clean_dets)} detections — bug in generator or detector")
        for _, r in clean_dets.head(10).iterrows():
            print("  -", dict(r))
        return 1
    print("\nclean set: 0 detections (OK)")
    return 0

if __name__ == "__main__":
    sys.exit(main())