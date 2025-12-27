from app.domain.chat.models import ChatTurnRequest, ParsedFields
from app.domain.chat.state_machine import handle_turn
from app.domain.pricing.config_loader import load_pricing_config

CONFIG = load_pricing_config("pricing/economy_v1.json")


def run_dialogue(messages):
    state = None
    last_response = None
    for message in messages:
        request = ChatTurnRequest(session_id="test", message=message)
        response, state = handle_turn(request, state, CONFIG)
        last_response = response
    return last_response


def test_dialogue_one():
    response = run_dialogue([
        "Hi, can I get a quote?",
        "2 bed 1 bath standard",
    ])
    assert response.estimate is not None
    assert response.missing_fields == []


def test_dialogue_two():
    response = run_dialogue([
        "Need a deep clean for 3 bed 2 bath with oven",
    ])
    assert response.estimate is not None


def test_dialogue_three():
    response = run_dialogue([
        "Looking to book cleaning",
        "2 bed",
        "2 bath deep",
    ])
    assert response.estimate is not None
    assert response.missing_fields == []


def test_dialogue_four():
    response = run_dialogue([
        "Move out clean 4 bed 3 bath",
    ])
    assert response.estimate is not None


def test_dialogue_five():
    response = run_dialogue([
        "Standard cleaning for 1 bed",
        "1 bath",
    ])
    assert response.estimate is not None


def test_dialogue_six():
    response = run_dialogue([
        "I need a quote",
        "3 bed 2 bath standard with fridge weekly",
    ])
    assert response.estimate is not None


def test_dialogue_seven():
    response = run_dialogue([
        "Looking for a deep clean",
        "2 bed 1.5 bath",
    ])
    assert response.estimate is not None


def test_dialogue_eight():
    response = run_dialogue([
        "2 bed 1 bath standard, oven and fridge",
    ])
    assert response.estimate is not None


def test_bathroom_follow_up_from_context():
    state = None
    request = ChatTurnRequest(session_id="context-test", message="Need a deep clean 4 bed")
    first_response, state = handle_turn(request, state, CONFIG)

    assert state.awaiting_field == "baths"
    assert "bath" in (first_response.proposed_questions[0].lower())

    next_request = ChatTurnRequest(session_id="context-test", message="2")
    second_response, state = handle_turn(next_request, state, CONFIG)

    assert state.baths == 2
    assert state.awaiting_field is None
    assert "baths" not in second_response.missing_fields
