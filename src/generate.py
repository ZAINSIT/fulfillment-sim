"""Master-table generator. Run:

    python -m src.generate --seed 20261022 --scale starter
    python -m src.generate --seed 20261022 --scale full

Scale controls row counts only. With the same seed, master tables are
byte-identical between runs at the same scale — that is the reproducibility
claim we will verify in Step 10."""

from __future__ import annotations
import argparse, hashlib, json, os
from datetime import date, datetime, timedelta, timezone
import pandas as pd
from src.ids import (
    sku_id, node_id, carrier_id, inventory_id, capacity_id, service_level_id,
)
from src.rng import build_rng
from src.schemas import (
    SKU, FulfillmentNode, Inventory, WarehouseCapacity, Carrier, ServiceLevel,
    Category, NodeType, Zone, ServiceLevelName,
)

import pathlib
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "master"
SEED_START = date(2026, 1, 1)
HORIZON_DAYS = 90  # capacity rows cover Q1 2026

def _scale(scale: str) -> dict:
    if scale == "starter":
        return dict(n_skus=200, n_nodes=20, n_carriers=10, inv_per_node=100)
    if scale == "full":
        return dict(n_skus=2000, n_nodes=20, n_carriers=10, inv_per_node=500)
    raise SystemExit(f"unknown scale {scale}")

def gen_skus(rng, fk, n: int) -> pd.DataFrame:
    rows = []
    cats = list(Category)
    for i in range(1, n + 1):
        cat = cats[int(rng.integers(0, len(cats)))]
        price = int(max(1, rng.lognormal(mean=7.5, sigma=0.9)))
        weight = int(rng.integers(50, 5000))
        hazmat = bool(rng.random() < 0.02 and cat == Category.electronics)
        active_from = SEED_START + timedelta(days=int(rng.integers(0, 30)))
        rec = SKU(
            sku_id=sku_id(i),
            name=fk.word().title() + " " + cat.value.title(),
            category=cat,
            list_price_paise=price,
            weight_g=weight,
            hazmat=hazmat,
            active_from=active_from,
            active_to=None,
        )
        rows.append(rec.model_dump())
    return pd.DataFrame(rows)

def gen_nodes(rng, n: int) -> pd.DataFrame:
    rows = []
    types = list(NodeType)
    zones = list(Zone)
    for i in range(1, n + 1):
        rec = FulfillmentNode(
            node_id=node_id(i),
            node_type=types[int(rng.integers(0, len(types)))],
            zone=zones[int(rng.integers(0, len(zones)))],
            daily_capacity_units=int(rng.integers(500, 5000)),
            cutoff_hour_local=int(rng.integers(14, 22)),
            timezone="Asia/Kolkata",
            active=True,
        )
        rows.append(rec.model_dump())
    return pd.DataFrame(rows)

def gen_inventory(rng, skus: pd.DataFrame, nodes: pd.DataFrame, per_node: int) -> pd.DataFrame:
    rows = []
    sku_ids = skus["sku_id"].tolist()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for _, node in nodes.iterrows():
        # deterministic sample of SKUs per node — same seed → same sample
        idx = rng.choice(len(sku_ids), size=min(per_node, len(sku_ids)), replace=False)
        for j in idx:
            sku = sku_ids[int(j)]
            on_hand = int(rng.integers(0, 500))
            reserved = int(rng.integers(0, on_hand + 1)) if on_hand else 0
            safety = int(rng.integers(0, on_hand + 1)) if on_hand else 0
            # safety_stock must be <= on_hand and reserved <= on_hand; clamp
            safety = min(safety, on_hand)
            reserved = min(reserved, on_hand - safety) if on_hand - safety >= 0 else 0
            rec = Inventory(
                inventory_id=inventory_id(node["node_id"], sku),
                node_id=node["node_id"],
                sku_id=sku,
                on_hand=on_hand,
                reserved=reserved,
                safety_stock=safety,
                as_of_ts=now,
            )
            rows.append(rec.model_dump())
    return pd.DataFrame(rows)

def gen_capacity(rng, nodes: pd.DataFrame) -> pd.DataFrame:
    rows = []
    holidays = {(1, 26), (3, 8), (12, 25)}  # Republic Day, Holi-ish, Christmas
    for _, node in nodes.iterrows():
        base_cap = int(node["daily_capacity_units"])
        for d in range(HORIZON_DAYS):
            day = SEED_START + timedelta(days=d)
            is_holiday = (day.month, day.day) in holidays
            cap = 0 if is_holiday else base_cap
            used = 0 if is_holiday else int(rng.integers(0, cap // 2 + 1))
            rec = WarehouseCapacity(
                capacity_id=capacity_id(node["node_id"], day.isoformat()),
                node_id=node["node_id"],
                date=day,
                units_capacity=cap,
                units_used=used,
                is_holiday=is_holiday,
            )
            rows.append(rec.model_dump())
    return pd.DataFrame(rows)

def gen_carriers(rng, n: int) -> pd.DataFrame:
    rows = []
    zones = list(Zone)
    levels = list(ServiceLevelName)
    for i in range(1, n + 1):
        zone_subset = [z for z in zones if rng.random() < 0.75] or [zones[0]]
        level_subset = [l for l in levels if rng.random() < 0.6] or [levels[0]]
        rec = Carrier(
            carrier_id=carrier_id(i),
            name=f"Carrier-{i:02d}",
            supports_hazmat=bool(rng.random() < 0.4),
            service_zones=zone_subset,
            service_levels=level_subset,
            cutoff_hour_local=int(rng.integers(12, 22)),
            timezone="Asia/Kolkata",
        )
        d = rec.model_dump()
        d["service_zones"] = ",".join(z.value for z in zone_subset)
        d["service_levels"] = ",".join(l.value for l in level_subset)
        rows.append(d)
    return pd.DataFrame(rows)

def gen_service_levels() -> pd.DataFrame:
    rows = [
        dict(service_level_id=service_level_id("standard"), name="standard",
             transit_days_min=3, transit_days_max=5, cutoff_required=True),
        dict(service_level_id=service_level_id("express"), name="express",
             transit_days_min=1, transit_days_max=2, cutoff_required=True),
        dict(service_level_id=service_level_id("same_day"), name="same_day",
             transit_days_min=0, transit_days_max=0, cutoff_required=True),
    ]
    for r in rows:
        ServiceLevel(**{**r, "name": ServiceLevelName(r["name"])})
    return pd.DataFrame(rows)

def _checksum(df: pd.DataFrame) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=True).values).hexdigest()

def main(seed: int, scale: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    p = _scale(scale)
    rng, fk = build_rng(seed)
    skus = gen_skus(rng, fk, p["n_skus"])
    nodes = gen_nodes(rng, p["n_nodes"])
    carriers = gen_carriers(rng, p["n_carriers"])
    inv = gen_inventory(rng, skus, nodes, p["inv_per_node"])
    cap = gen_capacity(rng, nodes)
    svc = gen_service_levels()

    outputs = {
        "sku.csv": skus, "fulfillment_node.csv": nodes, "carrier.csv": carriers,
        "inventory.csv": inv, "warehouse_capacity.csv": cap, "service_level.csv": svc,
    }
    manifest = {"seed": seed, "scale": scale, "files": {}}
    for name, df in outputs.items():
        path = DATA_DIR / name
        df.to_csv(path, index=False)
        manifest["files"][name] = {
            "rows": int(len(df)),
            "sha256": _checksum(df),
        }
        print(f"{name:28s} rows={len(df):6d}  sha256={manifest['files'][name]['sha256'][:12]}")
    with open(REPO_ROOT / "source_manifest.json", "w") as f:
        json.dump(manifest, f, indent=2, default=str)
    print("\nwrote source_manifest.json")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--scale", choices=["starter", "full"], default="starter")
    args = ap.parse_args()
    main(args.seed, args.scale)