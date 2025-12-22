import math
from typing import Any, Dict

from app.domain.pricing.models import EstimateRequest, EstimateResponse, EstimateBreakdown, Frequency
from app.domain.pricing.config_loader import PricingConfig


def _lookup_base_hours(table: list[Dict[str, Any]], beds: int, baths: float) -> float:
    baths_int = math.ceil(baths)
    exact = [row for row in table if row["beds"] == beds and row["baths"] == baths_int]
    if exact:
        return float(exact[0]["hours"])
    candidates = [
        row for row in table if row["beds"] >= beds and row["baths"] >= baths_int
    ]
    if candidates:
        selected = sorted(candidates, key=lambda r: (r["beds"], r["baths"]))[0]
        return float(selected["hours"])
    max_row = sorted(table, key=lambda r: (r["beds"], r["baths"]))[-1]
    return float(max_row["hours"])


def _team_size(team_thresholds: list[Dict[str, Any]], cleaner_hours: float) -> int:
    for threshold in team_thresholds:
        if cleaner_hours <= threshold["max_cleaner_hours"]:
            return int(threshold["team_size"])
    return int(team_thresholds[-1]["team_size"])


def _round_up(value: float, step: float) -> float:
    return math.ceil(value / step) * step


def estimate(request: EstimateRequest, pricing: PricingConfig) -> EstimateResponse:
    config = pricing.data
    base_hours = _lookup_base_hours(config["base_hours_table"], request.beds, request.baths)
    multiplier = config["multipliers"][request.cleaning_type.value]
    extra_hours = 0.0

    if request.heavy_grease:
        for tier in config["heavy_grease_extra_hours"]:
            if request.beds <= tier["max_beds"]:
                extra_hours += float(tier["extra_hours"])
                break
    if request.multi_floor:
        extra_hours += float(config["multi_floor_extra_hours"])

    total_cleaner_hours = (base_hours * multiplier) + extra_hours
    min_cleaner_hours = float(config["min_cleaner_hours"])
    total_cleaner_hours = max(total_cleaner_hours, min_cleaner_hours)

    team_size = _team_size(config["team_size_thresholds"], total_cleaner_hours)
    time_on_site = _round_up(total_cleaner_hours / team_size, float(config["time_on_site_rounding_hours"]))
    billed_cleaner_hours = team_size * time_on_site

    labor_cost = billed_cleaner_hours * float(config["rate_per_cleaner_hour"])

    add_ons = request.add_ons
    add_on_prices = config["add_ons"]
    add_ons_cost = 0.0
    add_ons_cost += add_on_prices["oven"] if add_ons.oven else 0
    add_ons_cost += add_on_prices["fridge"] if add_ons.fridge else 0
    add_ons_cost += add_on_prices["microwave"] if add_ons.microwave else 0
    add_ons_cost += add_on_prices["cabinets"] if add_ons.cabinets else 0
    add_ons_cost += add_on_prices["windows_up_to_5"] if add_ons.windows_up_to_5 else 0
    add_ons_cost += add_on_prices["balcony"] if add_ons.balcony else 0
    add_ons_cost += add_on_prices["linen_per_bed"] * add_ons.linen_beds
    add_ons_cost += add_on_prices["steam_armchair"] * add_ons.steam_armchair
    add_ons_cost += add_on_prices["steam_sofa_2"] * add_ons.steam_sofa_2
    add_ons_cost += add_on_prices["steam_sofa_3"] * add_ons.steam_sofa_3
    add_ons_cost += add_on_prices["steam_sectional"] * add_ons.steam_sectional
    add_ons_cost += add_on_prices["steam_mattress"] * add_ons.steam_mattress
    add_ons_cost += add_on_prices["carpet_spot"] * add_ons.carpet_spot

    discount = 0.0
    if request.frequency in (Frequency.weekly, Frequency.biweekly):
        discount = config["recurring_discounts"][request.frequency.value]
    discount_amount = labor_cost * discount

    total_before_tax = labor_cost - discount_amount + add_ons_cost

    breakdown = EstimateBreakdown(
        base_hours=base_hours,
        multiplier=multiplier,
        extra_hours=extra_hours,
        total_cleaner_hours=total_cleaner_hours,
        min_cleaner_hours_applied=min_cleaner_hours,
        team_size=team_size,
        time_on_site_hours=time_on_site,
        billed_cleaner_hours=billed_cleaner_hours,
        labor_cost=round(labor_cost, 2),
        add_ons_cost=round(add_ons_cost, 2),
        discount_amount=round(discount_amount, 2),
        total_before_tax=round(total_before_tax, 2),
    )

    return EstimateResponse(
        pricing_config_id=pricing.pricing_config_id,
        pricing_config_version=pricing.pricing_config_version,
        config_hash=pricing.config_hash,
        confidence=1.0,
        breakdown=breakdown,
    )
