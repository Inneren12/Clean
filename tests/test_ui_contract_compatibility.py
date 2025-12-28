"""
Test backward compatibility and edge cases of UI contract extension (S2-A).
Verifies that existing plain-text responses still work with the new optional fields,
and that the new fields handle edge cases correctly.
"""

import pytest
from pydantic import ValidationError

from app.domain.chat.models import (
    ChatTurnResponse,
    Choice,
    ChoicesConfig,
    Intent,
    ParsedFields,
    StepInfo,
    SummaryField,
    SummaryPatch,
    UIHint,
)


def test_old_style_response_without_new_fields():
    """Test that old responses without new UI contract fields still validate."""
    response_data = {
        "session_id": "test-123",
        "intent": "QUOTE",
        "parsed_fields": {"beds": 2},
        "state": {},
        "missing_fields": ["baths"],
        "proposed_questions": ["How many bathrooms?"],
        "reply_text": "How many bathrooms do you have?",
        "handoff_required": False,
        "estimate": None,
        "confidence": 0.8,
        # New fields are NOT present - should still work
    }

    response = ChatTurnResponse(**response_data)

    assert response.reply_text == "How many bathrooms do you have?"
    assert response.proposed_questions == ["How many bathrooms?"]
    assert response.choices is None
    assert response.step_info is None
    assert response.summary_patch is None
    assert response.ui_hint is None


def test_new_style_response_with_ui_contract_fields():
    """Test that new responses with all UI contract fields validate correctly."""
    response_data = {
        "session_id": "test-456",
        "intent": "QUOTE",
        "parsed_fields": {},
        "state": {},
        "missing_fields": ["cleaning_type"],
        "proposed_questions": [],
        "reply_text": "What type of cleaning do you need?",
        "handoff_required": False,
        "estimate": None,
        "confidence": 0.9,
        "choices": {
            "items": [
                {"id": "regular", "label": "Regular Cleaning"},
                {"id": "deep", "label": "Deep Cleaning"},
            ],
            "multi_select": False,
            "selection_type": "chip",
        },
        "step_info": {"current_step": 2, "total_steps": 5},
        "summary_patch": None,
        "ui_hint": {"show_choices": True},
    }

    response = ChatTurnResponse(**response_data)

    assert response.reply_text == "What type of cleaning do you need?"
    assert response.choices is not None
    assert len(response.choices.items) == 2
    assert response.step_info is not None
    assert response.step_info.current_step == 2
    assert response.step_info.total_steps == 5
    assert response.ui_hint is not None
    assert response.ui_hint.show_choices is True


def test_mixed_response_with_partial_new_fields():
    """Test response with some new fields present and others null."""
    response_data = {
        "session_id": "test-789",
        "intent": "QUOTE",
        "parsed_fields": {"beds": 3, "baths": 2},
        "state": {},
        "missing_fields": [],
        "proposed_questions": ["Book a slot", "Request callback"],
        "reply_text": "Great! Here's your estimate...",
        "handoff_required": False,
        "estimate": None,
        "confidence": 0.95,
        "choices": None,
        "step_info": {"current_step": 5, "total_steps": 5},
        "summary_patch": None,
        "ui_hint": None,
    }

    response = ChatTurnResponse(**response_data)

    assert response.reply_text == "Great! Here's your estimate..."
    assert response.step_info is not None
    assert response.step_info.current_step == 5
    assert response.choices is None
    assert response.ui_hint is None


def test_ui_hint_with_only_one_flag_set():
    """Test that UIHint with only one flag set doesn't implicitly disable others."""
    hint = UIHint(show_summary=True)

    assert hint.show_summary is True
    assert hint.show_confirm is None  # Not False - absence means "use frontend defaults"
    assert hint.show_choices is None
    assert hint.show_progress is None
    assert hint.minimize_text is None


def test_ui_hint_all_none_by_default():
    """Test that UIHint fields default to None, not False."""
    hint = UIHint()

    assert hint.show_summary is None
    assert hint.show_confirm is None
    assert hint.show_choices is None
    assert hint.show_progress is None
    assert hint.minimize_text is None


def test_step_info_validation_current_step_must_be_at_least_1():
    """Test that StepInfo.current_step must be >= 1."""
    with pytest.raises(ValidationError) as exc_info:
        StepInfo(current_step=0, total_steps=5)

    assert "current_step" in str(exc_info.value)


def test_step_info_validation_total_steps_must_be_at_least_1():
    """Test that StepInfo.total_steps must be >= 1."""
    with pytest.raises(ValidationError) as exc_info:
        StepInfo(current_step=1, total_steps=0)

    assert "total_steps" in str(exc_info.value)


def test_step_info_validation_remaining_questions_cannot_be_negative():
    """Test that StepInfo.remaining_questions cannot be negative."""
    with pytest.raises(ValidationError) as exc_info:
        StepInfo(current_step=1, total_steps=5, remaining_questions=-1)

    assert "remaining_questions" in str(exc_info.value)


def test_step_info_valid_values():
    """Test that StepInfo accepts valid values."""
    step_info = StepInfo(
        current_step=3,
        total_steps=5,
        step_label="Property Details",
        remaining_questions=2,
    )

    assert step_info.current_step == 3
    assert step_info.total_steps == 5
    assert step_info.step_label == "Property Details"
    assert step_info.remaining_questions == 2


def test_summary_field_accepts_primitive_types():
    """Test that SummaryField.value accepts primitive types."""
    # String value
    field_str = SummaryField(key="name", label="Name", value="John")
    assert field_str.value == "John"

    # Integer value
    field_int = SummaryField(key="beds", label="Bedrooms", value=2)
    assert field_int.value == 2

    # Float value
    field_float = SummaryField(key="baths", label="Bathrooms", value=1.5)
    assert field_float.value == 1.5

    # Boolean value
    field_bool = SummaryField(key="grease", label="Heavy Grease", value=True)
    assert field_bool.value is True

    # None value
    field_none = SummaryField(key="optional", label="Optional", value=None)
    assert field_none.value is None


def test_choices_config_with_multi_select():
    """Test ChoicesConfig with multi-select enabled."""
    config = ChoicesConfig(
        items=[
            Choice(id="oven", label="Inside Oven"),
            Choice(id="fridge", label="Inside Fridge"),
        ],
        multi_select=True,
        selection_type="chip",
    )

    assert config.multi_select is True
    assert len(config.items) == 2
    assert config.selection_type == "chip"


def test_summary_patch_with_editable_fields():
    """Test SummaryPatch with multiple field types."""
    patch = SummaryPatch(
        title="Your Details",
        fields=[
            SummaryField(key="beds", label="Bedrooms", value=2, field_type="number"),
            SummaryField(
                key="type",
                label="Type",
                value="REGULAR",
                field_type="select",
                options=[
                    Choice(id="regular", label="Regular", value="REGULAR"),
                    Choice(id="deep", label="Deep", value="DEEP"),
                ],
            ),
            SummaryField(
                key="grease", label="Heavy Grease", value=False, field_type="boolean"
            ),
        ],
    )

    assert patch.title == "Your Details"
    assert len(patch.fields) == 3
    assert patch.fields[0].field_type == "number"
    assert patch.fields[1].field_type == "select"
    assert patch.fields[2].field_type == "boolean"


def test_response_with_proposed_questions_omitted():
    """Test that response works when proposed_questions is completely omitted."""
    response_data = {
        "session_id": "test-999",
        "intent": "QUOTE",
        "parsed_fields": {},
        "state": {},
        "missing_fields": [],
        "reply_text": "Thanks!",
        "handoff_required": False,
        # proposed_questions field is omitted entirely
    }

    response = ChatTurnResponse(**response_data)

    # Pydantic should use the default value
    assert response.proposed_questions == []
