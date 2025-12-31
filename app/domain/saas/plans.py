from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class PlanLimits:
    max_workers: int
    max_bookings_per_month: int
    storage_gb: int


@dataclass(frozen=True)
class Plan:
    plan_id: str
    name: str
    price_cents: int
    currency: str
    stripe_price_id: Optional[str]
    limits: PlanLimits


PLANS: Dict[str, Plan] = {
    "free": Plan(
        plan_id="free",
        name="Free",
        price_cents=0,
        currency="usd",
        stripe_price_id=None,
        limits=PlanLimits(max_workers=3, max_bookings_per_month=20, storage_gb=1),
    ),
    "pro": Plan(
        plan_id="pro",
        name="Pro",
        price_cents=9900,
        currency="usd",
        stripe_price_id=None,
        limits=PlanLimits(max_workers=50, max_bookings_per_month=500, storage_gb=20),
    ),
}


def get_plan(plan_id: str | None) -> Plan:
    if plan_id and plan_id in PLANS:
        return PLANS[plan_id]
    return PLANS["free"]
