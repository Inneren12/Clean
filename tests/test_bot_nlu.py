import pytest

from app.bot.nlu.engine import analyze_message
from app.bot.nlu.models import Intent


GOLDEN_MESSAGES = [
    {
        "text": "Book a deep clean 2 bed 1 bath tomorrow morning in Seattle",
        "intent": Intent.booking,
        "entities": {
            "beds": 2,
            "baths": 1,
            "service_type": "deep_clean",
            "time_window_label": "morning",
            "area": "Seattle",
        },
    },
    {
        "text": "What's the price for a 900 sq ft apartment?",
        "intent": Intent.price,
        "entities": {"square_feet": 900},
    },
    {
        "text": "What is included in a regular service?",
        "intent": Intent.scope,
        "entities": {"service_type": "regular"},
    },
    {
        "text": "Need to reschedule my appointment to Friday after 6pm",
        "intent": Intent.reschedule,
        "entities": {"time_window_start": "18:00", "time_window_day": "friday"},
    },
    {"text": "Please cancel my booking", "intent": Intent.cancel},
    {"text": "Status update for today's service", "intent": Intent.status},
    {"text": "Tell me about your company", "intent": Intent.faq},
    {"text": "I want to talk to a human representative", "intent": Intent.human},
    {"text": "I am angry about the last clean and want a refund", "intent": Intent.complaint},
    {
        "text": "нужно забронировать уборку 2x2 после ремонта",
        "intent": Intent.booking,
        "entities": {"beds": 2, "baths": 2, "service_type": "post_renovation"},
    },
    {
        "text": "Сколько стоит уборка 45 m2?",
        "intent": Intent.price,
        "entities": {"square_meters": 45},
    },
    {
        "text": "Can we move the cleaning to next Monday morning?",
        "intent": Intent.reschedule,
        "entities": {"time_window_label": "morning"},
    },
    {
        "text": "Do you handle post-renovation cleaning and what is included?",
        "intent": Intent.scope,
        "entities": {"service_type": "post_renovation"},
    },
    {
        "text": "Can you clean the oven and fridge during a deep clean?",
        "intent": Intent.scope,
        "entities": {"extras": {"oven", "fridge"}, "service_type": "deep_clean"},
    },
    {
        "text": "Need cleaning near Central Park area",
        "intent": Intent.booking,
        "entities": {"area": "Central Park area"},
    },
    {
        "text": "Have two dogs and need standard cleaning",
        "intent": Intent.booking,
        "entities": {"extras": {"pets"}, "service_type": "regular"},
    },
    {
        "text": "Schedule a move out clean for Friday morning",
        "intent": Intent.booking,
        "entities": {"service_type": "move_out", "time_window_label": "morning"},
    },
    {
        "text": "Cost for 3 br 2 bath townhome",
        "intent": Intent.price,
        "entities": {"beds": 3, "baths": 2},
    },
    {"text": "ETA on crew arrival?", "intent": Intent.status},
    {"text": "Can I speak with someone live?", "intent": Intent.human},
    {"text": "There is an issue, the oven was left dirty", "intent": Intent.complaint},
    {"text": "Stop the service next week", "intent": Intent.cancel},
    {
        "text": "Change the time to after 8pm",
        "intent": Intent.reschedule,
        "entities": {"time_window_start": "20:00"},
    },
    {
        "text": "What services include windows and carpet cleaning?",
        "intent": Intent.scope,
        "entities": {"extras": {"windows", "carpet"}},
    },
    {
        "text": "Quote for cleaning In Brooklyn 1200 sq ft",
        "intent": Intent.price,
        "entities": {"square_feet": 1200, "area": "Brooklyn"},
    },
    {
        "text": "Book cleaning by tomorrow evening",
        "intent": Intent.booking,
        "entities": {"time_window_label": "evening", "time_window_day": "tomorrow"},
    },
    {
        "text": "Need to reschedule to Friday after 6pm evening",
        "intent": Intent.reschedule,
        "entities": {
            "time_window_label": "evening",
            "time_window_start": "18:00",
            "time_window_day": "friday",
        },
    },
    {
        "text": "hello",
        "intent": Intent.faq,
        "max_confidence": 0.4,
    },
    {"text": "Any update? where is the cleaner?", "intent": Intent.status},
    {
        "text": "Move my slot to Sunday",
        "intent": Intent.reschedule,
    },
    {
        "text": "What's included? do you bring supplies?",
        "intent": Intent.scope,
    },
    {
        "text": "The service was bad and I'm not happy",
        "intent": Intent.complaint,
    },
    {
        "text": "I want to cancel my booking and get a refund",
        "intent": Intent.cancel,
    },
    {
        "text": "cancel and refund my booking",
        "intent": Intent.cancel,
    },
    {
        "text": "Schedule a clean in Book please",
        "intent": Intent.booking,
    },
    {
        "text": "записать уборку на завтра",
        "intent": Intent.booking,
    },
    {
        "text": "How much for post-renovation cleaning after construction?",
        "intent": Intent.price,
        "entities": {"service_type": "post_renovation"},
    },
    {"text": "someone real please", "intent": Intent.human},
]


@pytest.mark.parametrize("case", GOLDEN_MESSAGES)
def test_golden_messages(case):
    result = analyze_message(case["text"])
    assert result.intent == case["intent"]

    if "max_confidence" in case:
        assert result.confidence <= case["max_confidence"]
    else:
        assert result.confidence >= 0.25

    entities = case.get("entities")
    if entities:
        if "beds" in entities:
            assert result.entities.beds == entities["beds"]
        if "baths" in entities:
            assert result.entities.baths == entities["baths"]
        if "square_feet" in entities:
            assert result.entities.square_feet == entities["square_feet"]
        if "square_meters" in entities:
            assert result.entities.square_meters == entities["square_meters"]
        if "service_type" in entities:
            assert result.entities.service_type == entities["service_type"]
        if "extras" in entities:
            assert set(result.entities.extras) >= set(entities["extras"])
        if "time_window_label" in entities:
            assert result.entities.time_window is not None
            assert result.entities.time_window.label == entities["time_window_label"]
        if "time_window_start" in entities:
            assert result.entities.time_window is not None
            assert result.entities.time_window.start == entities["time_window_start"]
        if "time_window_day" in entities:
            assert result.entities.time_window is not None
            assert result.entities.time_window.day == entities["time_window_day"]
        if "area" in entities:
            assert result.entities.area == entities["area"]

    assert result.reasons, "reasons should explain scoring"


def test_low_confidence_flag():
    result = analyze_message("ok")
    assert result.intent == Intent.faq
    assert "low confidence" in result.reasons
    assert result.confidence <= 0.4
