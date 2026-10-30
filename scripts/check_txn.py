"""Validate transaction tables against the invariants in DATA_DICTIONARY.md §13."""

import sys, pathlib, pandas as pd

REPO = pathlib.Path(__file__).resolve().parent.parent
M = REPO / "data" / "master"
T = REPO / "data" / "txn"

def main() -> int:
    orders = pd.read_csv(T / "orders.csv")
    lines = pd.read_csv(T / "order_lines.csv")
    shps = pd.read_csv(T / "shipments.csv")
    proms = pd.read_csv(T / "delivery_promises.csv")
    trk = pd.read_csv(T / "tracking_events.csv")
    nodes = pd.read_csv(M / "fulfillment_node.csv")
    carriers = pd.read_csv(M / "carrier.csv")
    errs = []

    # PK uniqueness
    if orders["order_id"].duplicated().any(): errs.append("orders: dup PK")
    if lines["order_line_id"].duplicated().any(): errs.append("lines: dup PK")
    if shps["shipment_id"].duplicated().any(): errs.append("shipments: dup PK")
    if proms["promise_id"].duplicated().any(): errs.append("promises: dup PK")
    if trk["tracking_id"].duplicated().any(): errs.append("tracking: dup PK")

    # FK integrity
    if not set(lines["order_id"]).issubset(set(orders["order_id"])): errs.append("lines.order_id -> orders missing")
    if not set(shps["order_id"]).issubset(set(orders["order_id"])): errs.append("shipments.order_id -> orders missing")
    if not set(shps["node_id"]).issubset(set(nodes["node_id"])): errs.append("shipments.node_id -> nodes missing")
    if not set(shps["carrier_id"]).issubset(set(carriers["carrier_id"])): errs.append("shipments.carrier_id -> carriers missing")
    if not set(proms["order_id"]).issubset(set(orders["order_id"])): errs.append("promises.order_id -> orders missing")
    if not set(trk["shipment_id"]).issubset(set(shps["shipment_id"])): errs.append("tracking.shipment_id -> shipments missing")

    # Arithmetic: order total == sum(line totals)
    tot = lines.groupby("order_id")["line_total_paise"].sum()
    merged = orders.set_index("order_id")["order_total_paise"]
    bad = (tot.reindex(merged.index).fillna(0) != merged).sum()
    if bad: errs.append(f"orders: {bad} rows where order_total != sum(lines)")

    # Date ordering: promise >= order date
    o_ts = pd.to_datetime(orders.set_index("order_id")["order_ts"]).dt.date
    p_dt = pd.to_datetime(proms.set_index("order_id")["promised_date"]).dt.date
    bad = (p_dt < o_ts.reindex(p_dt.index)).sum()
    if bad: errs.append(f"promises: {bad} rows before order date")

    # Shipment date ordering
    sh = shps.copy()
    sh["dispatch_ts"] = pd.to_datetime(sh["dispatch_ts"], errors="coerce")
    sh["delivered_ts"] = pd.to_datetime(sh["delivered_ts"], errors="coerce")
    bad = ((sh["dispatch_ts"].notna()) & (sh["delivered_ts"].notna()) &
           (sh["delivered_ts"] < sh["dispatch_ts"])).sum()
    if bad: errs.append(f"shipments: {bad} rows delivered < dispatched")

    # Tracking monotonic per shipment
    tr = trk.copy()
    tr["event_ts"] = pd.to_datetime(tr["event_ts"])
    for sid, g in tr.groupby("shipment_id"):
        if not g["event_ts"].is_monotonic_increasing:
            errs.append(f"tracking: shipment {sid} not monotonic"); break

    if errs:
        print("FAIL"); [print("  -", e) for e in errs]; return 1
    print(f"OK  orders={len(orders)} lines={len(lines)} shipments={len(shps)} "
          f"promises={len(proms)} tracking={len(trk)}")
    return 0

if __name__ == "__main__":
    sys.exit(main())