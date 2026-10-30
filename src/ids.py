"""Deterministic ID construction. No randomness here — IDs are a pure
function of (entity, counter). This is what makes them immutable and
reproducible across runs with the same seed."""

def order_id(n: int) -> str:      return f"ORD-2026-{n:06d}"
def node_id(n: int) -> str:       return f"FUL-2026-{n:06d}"
def shipment_id(n: int) -> str:   return f"SHP-2026-{n:06d}"
def sku_id(n: int) -> str:        return f"SKU-{n:06d}"
def customer_id(n: int) -> str:   return f"CUS-{n:06d}"
def carrier_id(n: int) -> str:    return f"CAR-{n:03d}"
def order_line_id(order: str, k: int) -> str:
    return f"OL-{order}-{k:02d}"
def inventory_id(node: str, sku: str) -> str:
    return f"INV-{node}-{sku}"
def capacity_id(node: str, date: str) -> str:
    return f"CAP-{node}-{date}"
def tracking_id(shipment: str, k: int) -> str:
    return f"TRK-{shipment}-{k:03d}"
def promise_id(order: str, rev: int) -> str:
    return f"PROM-{order}-{rev:02d}"
def service_level_id(name: str) -> str:
    return f"SL-{name}"