"""Transaction-table generator + decision engine entry point.

Reads master tables from data/master/, writes:
  orders.csv, order_lines.csv, shipments.csv, delivery_promises.csv,
  tracking_events.csv
to data/txn/.

The decision engine lives in src/decide.py. This module wires it in
deterministically, so with the same seed + master CSVs the txn CSVs are
byte-identical across runs.
"""

from __future__ import annotations
import argparse, hashlib, json, pathlib
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from src.ids import (order_id, order_line_id, shipment_id, tracking_id,
                     promise_id, customer_id)
from src.rng import build_rng
from src.decide import allocate_and_promise, ShipmentPlan

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
MASTER = REPO_ROOT / "data" / "master"
TXN = REPO_ROOT / "data" / "txn"

SEED_START = date(2026, 1, 1)
HORIZON_DAYS = 60          # orders fall in Jan–Feb 2026
NOW = datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc)

def _scale(scale: str) -> dict:
    if scale == "starter":
        return dict(n_orders=2000)
    if scale == "full":
        return dict(n_orders=50_000)
    raise SystemExit(f"unknown scale {scale}")

def load_master() -> dict:
    def r(n): return pd.read_csv(MASTER / n)
    return {
        "sku": r("sku.csv"),
        "node": r("fulfillment_node.csv"),
        "carrier": r("carrier.csv"),
        "inventory": r("inventory.csv"),
        "capacity": r("warehouse_capacity.csv"),
        "service_level": r("service_level.csv"),
    }

def _enum_list(s: str) -> list[str]:
    return [] if pd.isna(s) or s == "" else s.split(",")

def build_carriers(df: pd.DataFrame) -> list[dict]:
    out = []
    for _, row in df.iterrows():
        out.append(dict(
            carrier_id=row["carrier_id"],
            supports_hazmat=bool(row["supports_hazmat"]),
            zones=set(_enum_list(row["service_zones"])),
            levels=set(_enum_list(row["service_levels"])),
            cutoff=int(row["cutoff_hour_local"]),
        ))
    return out

def build_inventory(df: pd.DataFrame) -> dict[tuple[str, str], int]:
    """available_to_promise = on_hand - reserved - safety_stock."""
    atp = {}
    for _, row in df.iterrows():
        atp[(row["node_id"], row["sku_id"])] = int(
            row["on_hand"] - row["reserved"] - row["safety_stock"]
        )
    return atp

def build_capacity(df: pd.DataFrame) -> dict[tuple[str, date], int]:
    cap = {}
    for _, row in df.iterrows():
        d = date.fromisoformat(row["date"])
        cap[(row["node_id"], d)] = int(row["units_capacity"] - row["units_used"])
    return cap

def gen_orders(rng, fk, skus: pd.DataFrame, n: int):
    """Return (orders_df, lines_df). Each order has 1–4 lines."""
    orders, lines = [], []
    sku_ids = skus["sku_id"].tolist()
    price_by_sku = dict(zip(skus["sku_id"], skus["list_price_paise"]))
    hazmat_by_sku = dict(zip(skus["sku_id"], skus["hazmat"]))
    for i in range(1, n + 1):
        oid = order_id(i)
        day_off = int(rng.integers(0, HORIZON_DAYS))
        hour = int(rng.integers(0, 23))
        order_ts = NOW + timedelta(days=day_off, hours=hour)
        zone = f"Z{int(rng.integers(1, 9))}"
        service = ["standard", "express", "same_day"][int(rng.integers(0, 3))]
        n_lines = int(rng.integers(1, 5))
        chosen = rng.choice(len(sku_ids), size=n_lines, replace=False)
        order_total = 0
        for k, idx in enumerate(sorted(chosen), start=1):
            sku = sku_ids[int(idx)]
            qty = int(rng.integers(1, 4))
            unit = price_by_sku[sku]
            total = qty * unit
            order_total += total
            lines.append(dict(
                order_line_id=order_line_id(oid, k),
                order_id=oid,
                sku_id=sku,
                qty=qty,
                unit_price_paise=unit,
                line_total_paise=total,
                hazmat=hazmat_by_sku[sku],
            ))
        orders.append(dict(
            order_id=oid,
            customer_id=customer_id(int(rng.integers(1, 5000))),
            order_ts=order_ts.isoformat(),
            channel=["web", "app", "store", "phone"][int(rng.integers(0, 4))],
            ship_to_zone=zone,
            requested_service=service,
            order_total_paise=order_total,
            lifecycle_state="created",
            created_at=order_ts.isoformat(),
        ))
    return pd.DataFrame(orders), pd.DataFrame(lines)

def main(seed: int, scale: str) -> None:
    TXN.mkdir(parents=True, exist_ok=True)
    p = _scale(scale)
    rng, fk = build_rng(seed)
    master = load_master()

    orders, lines = gen_orders(rng, fk, master["sku"], p["n_orders"])

    carriers = build_carriers(master["carrier"])
    inv_atp = build_inventory(master["inventory"])
    cap_rem = build_capacity(master["capacity"])
    node_zone = dict(zip(master["node"]["node_id"], master["node"]["zone"]))
    node_cutoff = dict(zip(master["node"]["node_id"], master["node"]["cutoff_hour_local"]))
    svc_transit = {r["name"]: (int(r["transit_days_min"]), int(r["transit_days_max"]))
                   for _, r in master["service_level"].iterrows()}

    shipments, promises, tracking = [], [], []
    shp_ctr = 1
    orders_by_id = {r["order_id"]: r for _, r in orders.iterrows()}
    lines_by_order = defaultdict(list)
    for _, r in lines.iterrows():
        lines_by_order[r["order_id"]].append(r)

    for _, o in orders.iterrows():
        o_lines = lines_by_order[o["order_id"]]
        plans, promise = allocate_and_promise(
            order=o,
            lines=[dict(r) for r in o_lines],
            carriers=carriers,
            inv_atp=inv_atp,
            cap_rem=cap_rem,
            node_zone=node_zone,
            node_cutoff=node_cutoff,
            svc_transit=svc_transit,
            rng=rng,
        )
        # materialise shipments + tracking
        order_state = "allocated"
        shp_ids = []
        for plan in plans:
            sid = shipment_id(shp_ctr); shp_ctr += 1
            shp_ids.append(sid)
            dispatch = plan.dispatch_ts
            delivered = plan.delivered_ts
            status = "delivered" if delivered else ("in_transit" if dispatch else "planned")
            shipments.append(dict(
                shipment_id=sid,
                order_id=o["order_id"],
                node_id=plan.node_id,
                carrier_id=plan.carrier_id,
                service_level=plan.service_level,
                dispatch_ts=dispatch.isoformat() if dispatch else "",
                delivered_ts=delivered.isoformat() if delivered else "",
                status=status,
                is_split=len(plans) > 1,
                parent_shipment_id="",
                qty=plan.qty,
                sku_id=plan.sku_id,
                order_line_id=plan.order_line_id,
            ))
            # tracking events: picked -> departed -> arrived -> out_for_delivery -> delivered
            events = plan.tracking_events(sid)
            for k, ev in enumerate(events, start=1):
                tracking.append(dict(
                    tracking_id=tracking_id(sid, k),
                    shipment_id=sid,
                    event_ts=ev["ts"].isoformat(),
                    event_type=ev["type"],
                    location_zone=ev["zone"],
                    note="",
                ))
        if all(s["status"] == "delivered" for s in shipments if s["order_id"] == o["order_id"]):
            order_state = "delivered"
        elif any(s["status"] in ("dispatched", "in_transit") for s in shipments if s["order_id"] == o["order_id"]):
            order_state = "shipped"
        orders.at[_, "lifecycle_state"] = order_state
        promises.append(dict(
            promise_id=promise_id(o["order_id"], 1),
            order_id=o["order_id"],
            promised_date=promise.promised_date.isoformat(),
            promised_service=promise.promised_service,
            node_id=promise.node_id,
            reason_code=promise.reason_code,
            created_at=o["order_ts"],
            supersedes_promise_id="",
        ))

    frames = {
        "orders.csv": orders,
        "order_lines.csv": lines,
        "shipments.csv": pd.DataFrame(shipments),
        "delivery_promises.csv": pd.DataFrame(promises),
        "tracking_events.csv": pd.DataFrame(tracking),
    }
    manifest = {"seed": seed, "scale": scale, "files": {}}
    for name, df in frames.items():
        path = TXN / name
        df.to_csv(path, index=False)
        manifest["files"][name] = {
            "rows": int(len(df)),
            "sha256": hashlib.sha256(pd.util.hash_pandas_object(df, index=True).values).hexdigest(),
        }
        print(f"{name:24s} rows={len(df):7d}  sha256={manifest['files'][name]['sha256'][:12]}")
    with open(REPO_ROOT / "source_manifest_txn.json", "w") as f:
        json.dump(manifest, f, indent=2, default=str)
    print("\nwrote source_manifest_txn.json")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--scale", choices=["starter", "full"], default="starter")
    args = ap.parse_args()
    main(args.seed, args.scale)