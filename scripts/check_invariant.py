"""Run after generate.py. Verifies PK uniqueness and FK integrity on the
master tables before any transaction data is allowed to exist."""

import sys, pathlib, pandas as pd

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
D = REPO_ROOT / "data" / "master"
def load(n): return pd.read_csv(D / n)

def main() -> int:
    sku = load("sku.csv")
    node = load("fulfillment_node.csv")
    car = load("carrier.csv")
    inv = load("inventory.csv")
    cap = load("warehouse_capacity.csv")
    svc = load("service_level.csv")
    errs = []

    # PKs
    for name, df, col in [
        ("sku", sku, "sku_id"), ("node", node, "node_id"),
        ("carrier", car, "carrier_id"), ("inventory", inv, "inventory_id"),
        ("capacity", cap, "capacity_id"), ("service_level", svc, "service_level_id"),
    ]:
        if df[col].duplicated().any():
            errs.append(f"{name}: duplicate PK in {col}")

    # FKs
    if not set(inv["node_id"]).issubset(set(node["node_id"])):
        errs.append("inventory.node_id not in fulfillment_node")
    if not set(inv["sku_id"]).issubset(set(sku["sku_id"])):
        errs.append("inventory.sku_id not in sku")
    if not set(cap["node_id"]).issubset(set(node["node_id"])):
        errs.append("capacity.node_id not in fulfillment_node")

    # Enum + range checks the pydantic layer already enforced — re-check post-CSV
    if not sku["list_price_paise"].gt(0).all():
        errs.append("sku: list_price_paise <= 0")
    if not (inv["reserved"] <= inv["on_hand"]).all():
        errs.append("inventory: reserved > on_hand")
    if not (inv["safety_stock"] <= inv["on_hand"]).all():
        errs.append("inventory: safety_stock > on_hand")
    if not (cap["units_used"] <= cap["units_capacity"]).all():
        errs.append("capacity: units_used > units_capacity")

    if errs:
        print("FAIL"); [print("  -", e) for e in errs]; return 1
    print(f"OK  sku={len(sku)} node={len(node)} carrier={len(car)} "
          f"inventory={len(inv)} capacity={len(cap)} service_level={len(svc)}")
    return 0

if __name__ == "__main__":
    sys.exit(main())