import pytest

from app.domain.pricing.config_loader import load_pricing_config
from app.domain.pricing.estimator import estimate
from app.domain.pricing.models import EstimateRequest, AddOns, CleaningType, Frequency


CONFIG = load_pricing_config("pricing/economy_v1.json")


def test_standard_base_case():
    request = EstimateRequest(beds=2, baths=1, cleaning_type=CleaningType.standard)
    response = estimate(request, CONFIG)
    assert response.breakdown.base_hours == 3.0
    assert response.breakdown.labor_cost == 105.0
    assert response.breakdown.total_before_tax == 105.0
    assert response.labor_cost == response.breakdown.labor_cost
    assert response.total_before_tax == response.breakdown.total_before_tax


def test_rounding_up_time_on_site():
    request = EstimateRequest(
        beds=2,
        baths=1,
        cleaning_type=CleaningType.deep,
        multi_floor=True,
    )
    response = estimate(request, CONFIG)
    assert response.breakdown.total_cleaner_hours == 4.1
    assert response.breakdown.time_on_site_hours == 4.5
    assert response.breakdown.billed_cleaner_hours == 4.5


def test_min_cleaner_hours_enforced():
    request = EstimateRequest(beds=1, baths=1, cleaning_type=CleaningType.standard)
    response = estimate(request, CONFIG)
    assert response.breakdown.total_cleaner_hours == 3.0
    assert response.breakdown.labor_cost == 105.0


def test_team_size_two():
    request = EstimateRequest(
        beds=3,
        baths=2,
        cleaning_type=CleaningType.standard,
        heavy_grease=True,
    )
    response = estimate(request, CONFIG)
    assert response.breakdown.total_cleaner_hours == 5.0
    assert response.breakdown.team_size == 2
    assert response.breakdown.time_on_site_hours == 2.5


def test_team_size_three():
    request = EstimateRequest(
        beds=6,
        baths=4,
        cleaning_type=CleaningType.move_out_empty,
    )
    response = estimate(request, CONFIG)
    assert response.breakdown.team_size == 3
    assert response.breakdown.billed_cleaner_hours == 9.0
    assert response.breakdown.labor_cost == 315.0


def test_heavy_grease_extra_hours():
    request = EstimateRequest(
        beds=2,
        baths=1,
        cleaning_type=CleaningType.standard,
        heavy_grease=True,
    )
    response = estimate(request, CONFIG)
    assert response.breakdown.extra_hours == 0.5
    assert response.breakdown.total_cleaner_hours == 3.5


def test_multi_floor_extra_hours():
    request = EstimateRequest(
        beds=2,
        baths=2,
        cleaning_type=CleaningType.standard,
        multi_floor=True,
    )
    response = estimate(request, CONFIG)
    assert response.breakdown.extra_hours == 0.5
    assert response.breakdown.total_cleaner_hours == 4.0


def test_add_ons_costs():
    add_ons = AddOns(
        oven=True,
        fridge=True,
        linen_beds=2,
        steam_sofa_2=1,
        carpet_spot=2,
    )
    request = EstimateRequest(
        beds=2,
        baths=1,
        cleaning_type=CleaningType.standard,
        add_ons=add_ons,
    )
    response = estimate(request, CONFIG)
    assert response.breakdown.add_ons_cost == 230.0
    assert response.breakdown.total_before_tax == 335.0


def test_weekly_discount_applied():
    request = EstimateRequest(
        beds=2,
        baths=1,
        cleaning_type=CleaningType.standard,
        frequency=Frequency.weekly,
    )
    response = estimate(request, CONFIG)
    assert response.breakdown.discount_amount == 10.5
    assert response.breakdown.total_before_tax == 94.5


def test_conservative_base_hours_lookup():
    request = EstimateRequest(beds=3, baths=1, cleaning_type=CleaningType.standard)
    response = estimate(request, CONFIG)
    assert response.breakdown.base_hours == 4.0


def test_move_out_multiplier():
    request = EstimateRequest(
        beds=2,
        baths=2,
        cleaning_type=CleaningType.move_out_empty,
    )
    response = estimate(request, CONFIG)
    assert response.breakdown.total_cleaner_hours == pytest.approx(4.725)
    assert response.breakdown.time_on_site_hours == 2.5


def test_bath_rounding():
    request = EstimateRequest(beds=2, baths=1.5, cleaning_type=CleaningType.standard)
    response = estimate(request, CONFIG)
    assert response.breakdown.base_hours == 3.5
    assert response.breakdown.total_cleaner_hours == 3.5
