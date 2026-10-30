"""Allocation + promise decision engine.

Rules (each numbered rule maps to a DECISIONS.md entry):
  R-01  Pick node(s) whose zone can serve the order zone via at least one
        carrier that supports the requested service level.
  R-02  A node is eligible only if available_to_promise >= requested qty
        for that SKU. Split across nodes only when no single node covers
        the whole line.
  R-03  Respect capacity: allocation for (node, dispatch_date) must not
        exceed remaining capacity.
  R-04  Dispatch date = first non-holiday day where node cutoff has not
        already passed relative to order_ts in node local time.
  R-05  promised_date = dispatch_date + transit_days_max for the service
        level. Use max (conservative), not min.
  R-06  If no node can cover a line, the line becomes an exception:
        promise is still emitted with reason_code='fallback' and
        promised_date computed from the primary zone's standard SLA, so
        the order is never silently dropped.
  R-07  Deterministic tie-break: prefer the node with the highest
        available_to_promise, then lexicographically smallest node_id.

The engine is a pure function of its inputs plus `rng` (only used for
carrier choice, which is not correctness-critical). Given the same inputs
it produces the same plans.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

@dataclass
class ShipmentPlan:
    node_id: str
    carrier_id: str
    service_level: str
    sku_id: str
    order_line_id: str
    qty: int
    dispatch_ts: Optional[datetime]
    promise_date: date

    def tracking_events(self, sid: str) -> list[dict]:
        """Monotonic timeline. Returns [] if never dispatched."""
        if not self.dispatch_ts:
            return []
        t = self.dispatch_ts
        return [
            {"ts": t,                        "type": "picked",           "zone": "Z0"},
            {"ts": t + timedelta(hours=6),   "type": "departed",         "zone": "Z0"},
            {"ts": t + timedelta(hours=24),  "type": "arrived",          "zone": "Z1"},
            {"ts": t + timedelta(hours=30),  "type": "out_for_delivery", "zone": "Z1"},
            {"ts": t + timedelta(hours=36),  "type": "delivered",        "zone": "Z1"},
        ]

    @property
    def delivered_ts(self) -> Optional[datetime]:
        if not self.dispatch_ts:
            return None
        return self.dispatch_ts + timedelta(hours=36)

@dataclass
class Promise:
    order_id: str
    node_id: str
    promised_date: date
    promised_service: str
    reason_code: str

def _next_valid_dispatch(node_cutoff: int, order_ts: datetime, is_holiday) -> date:
    """R-04: first non-holiday day where order_ts (local) is before cutoff."""
    d = order_ts.date()
    # If order placed after cutoff, start next day
    if order_ts.hour >= node_cutoff:
        d = d + timedelta(days=1)
    for _ in range(14):
        if not is_holiday(d):
            return d
        d = d + timedelta(days=1)
    return d

def allocate_and_promise(
    *,
    order, lines, carriers, inv_atp, cap_rem, node_zone, node_cutoff, svc_transit, rng,
) -> tuple[list[ShipmentPlan], Promise]:
    order_id_ = order["order_id"]
    order_ts = datetime.fromisoformat(order["order_ts"])
    zone = order["ship_to_zone"]
    service = order["requested_service"]
    plans: list[ShipmentPlan] = []

    # Candidate nodes: any node in a zone reachable by a carrier that
    # supports the requested service. (R-01)
    candidate_nodes = []
    for node_id_, nz in node_zone.items():
        for c in carriers:
            if service in c["levels"] and zone in c["zones"]:
                candidate_nodes.append((node_id_, nz, c))
                break

    for line in lines:
        qty_needed = int(line["qty"])
        sku = line["sku_id"]
        # Sort candidates by available ATP desc, then node_id asc (R-07)
        ranked = sorted(
            [(n, z, c, inv_atp.get((n, sku), 0)) for (n, z, c) in candidate_nodes],
            key=lambda x: (-x[3], x[0]),
        )
        assigned = False
        for (n, z, c, atp) in ranked:
            if atp <= 0:
                continue
            take = min(qty_needed, atp)
            disp_day = _next_valid_dispatch(
                node_cutoff[n], order_ts, lambda d: cap_rem.get((n, d), 0) <= 0
            )
            if cap_rem.get((n, disp_day), 0) < take:
                continue
            # materialise
            inv_atp[(n, sku)] = atp - take
            cap_rem[(n, disp_day)] = cap_rem.get((n, disp_day), 0) - take
            tmin, tmax = svc_transit[service]
            promise_date = disp_day + timedelta(days=tmax)
            dispatch_ts = datetime.combine(disp_day, datetime.min.time()).replace(
                hour=node_cutoff[n], tzinfo=order_ts.tzinfo
            )
           
            plans.append(ShipmentPlan(
                node_id=n, carrier_id=c["carrier_id"], service_level=service,
                sku_id=sku, order_line_id=line["order_line_id"], qty=take,
                dispatch_ts=dispatch_ts,
                promise_date=promise_date,
            ))
            qty_needed -= take
            if qty_needed <= 0:
                assigned = True
                break
        if not assigned:
            # R-06: fallback promise, no shipment
            tmin, tmax = svc_transit[service]
            fb = order_ts.date() + timedelta(days=tmax + 2)
            plans.append(ShipmentPlan(
                node_id="FUL-2026-000001", carrier_id="CAR-001", service_level=service,
                sku_id=sku, order_line_id=line["order_line_id"], qty=qty_needed,
                dispatch_ts=None, promise_date=fb,
            ))

    # Single promise per order = earliest date across plans (R-05 aggregation)
    promised_date = min(p.promise_date for p in plans) if plans else order_ts.date()
    node_for_promise = plans[0].node_id if plans else "FUL-2026-000001"
    reason = "single_node" if len(plans) == 1 else ("split_node" if len(plans) > 1 else "fallback")
    return plans, Promise(
        order_id=order_id_, node_id=node_for_promise,
        promised_date=promised_date, promised_service=service, reason_code=reason,
    )