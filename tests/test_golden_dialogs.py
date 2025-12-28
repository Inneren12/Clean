import json
from pathlib import Path
from typing import Any, Dict

import pytest

from app.domain.chat.models import ChatTurnRequest
from app.domain.chat.state_machine import handle_turn
from app.domain.pricing.config_loader import load_pricing_config

GOLDEN_PATH = Path(__file__).parent / "golden_dialogs" / "dialogs.json"
GOLDENS = json.loads(GOLDEN_PATH.read_text())


@pytest.fixture(scope="session")
def pricing_config():
    pricing_path = Path(__file__).resolve().parents[1] / "pricing" / "economy_v1.json"
    return load_pricing_config(str(pricing_path))

STEP_LABEL_TO_STAGE = {
    "Property details": "collect",
    "Price estimate": "price",
    "Confirm price": "confirm",
    "Contact info": "contact",
    "Complete": "created",
}


def _assert_fields(parsed_fields, expected_fields: Dict[str, Any]):
    for key, expected in expected_fields.items():
        if key == "add_ons":
            add_on_values = parsed_fields.add_ons.model_dump()
            for add_on_key, add_on_expected in expected.items():
                assert (
                    add_on_values.get(add_on_key) == add_on_expected
                ), f"add-on {add_on_key}"
        else:
            assert getattr(parsed_fields, key) == expected, key


def _assert_estimate(response, expected_estimate: Dict[str, Any]):
    assert response.estimate is not None, "expected an estimate"
    for field, expected in expected_estimate.items():
        actual = getattr(response.estimate, field)
        assert actual == pytest.approx(expected), field


def _derive_stage(response) -> str | None:
    if response.handoff_required:
        return "handoff"
    if response.missing_fields:
        return "collect"

    step_info = response.step_info
    if step_info:
        if step_info.remaining_questions:
            return "collect"
        if step_info.current_step and step_info.total_steps:
            if step_info.current_step >= step_info.total_steps:
                return "created"
            if step_info.current_step == step_info.total_steps - 1:
                return "contact"
            if step_info.current_step == step_info.total_steps - 2:
                return "price"
        step_label = (step_info.step_label or "").strip().lower()
        for label, stage in STEP_LABEL_TO_STAGE.items():
            if step_label.startswith(label.lower()):
                return stage
    if response.estimate:
        return "price"
    return None


def _assert_reply_contains(response, snippet: str):
    assert snippet in response.reply_text, f"expected '{snippet}' in reply"


def _assert_proposed_question(response, snippet: str):
    joined = " ".join(response.proposed_questions)
    assert snippet in joined, f"expected '{snippet}' in proposed questions"


@pytest.mark.parametrize("dialogue", GOLDENS, ids=[case["id"] for case in GOLDENS])
def test_golden_dialogs(dialogue, pricing_config):
    state = None
    for turn in dialogue["turns"]:
        request = ChatTurnRequest(session_id=dialogue["id"], message=turn["user"])
        response, state = handle_turn(request, state, pricing_config)
        expected = turn.get("expect", {})

        if "intent" in expected:
            expected_intent = str(expected["intent"]).upper()
            actual_intent = (response.intent.name or response.intent.value).upper()
            assert actual_intent == expected_intent

        if "stage" in expected:
            assert _derive_stage(response) == expected["stage"]

        if "missing_fields" in expected:
            assert response.missing_fields == expected["missing_fields"]

        if "fields" in expected:
            _assert_fields(response.parsed_fields, expected["fields"])

        if "estimate" in expected:
            _assert_estimate(response, expected["estimate"])
        else:
            # Ensure we do not accidentally expose an estimate when not requested
            if expected.get("handoff_required"):
                assert response.estimate is None

        if "reply_contains" in expected:
            _assert_reply_contains(response, expected["reply_contains"])

        if "proposed_question_contains" in expected:
            _assert_proposed_question(response, expected["proposed_question_contains"])

        if "handoff_required" in expected:
            assert response.handoff_required is expected["handoff_required"]
