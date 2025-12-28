from app.bot.nlu.models import Intent
from app.bot.rules.engine import RulesEngine
from app.domain.bot.schemas import FsmStep


def test_upsell_rules_apply_keywords_and_reasons():
    engine = RulesEngine()
    result = engine.apply_upsells("Please include windows and the fridge cleaning", {"extras": []})

    assert set(result.filled_fields.get("extras", [])) >= {"windows", "fridge"}
    assert any("windows" in reason.lower() for reason in result.reasons)


def test_checklist_fast_path_limits_steps():
    engine = RulesEngine()
    filled = {"service_type": "deep_clean", "beds": 2, "baths": 2}
    steps = engine.steps_for_intent(Intent.price, filled, fast_path=True)

    assert steps == [FsmStep.ask_area, FsmStep.ask_preferred_time]


def test_prep_instructions_available_for_special_services():
    engine = RulesEngine()
    filled = {"service_type": "move_out"}

    instructions = engine.prep_instructions(filled)
    assert instructions
    assert any("fridge" in tip.lower() for tip in instructions)
