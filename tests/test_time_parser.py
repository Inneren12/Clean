from datetime import date

from app.bot.time_parser import DEFAULT_TZ, parse_time_request


BASE_DATE = date(2024, 4, 29)  # Monday


def test_after_six_window_defaults_and_clarifier():
    result = parse_time_request("after 6", reference_date=BASE_DATE)

    assert result.confidence == "medium"
    assert result.time_window is not None
    assert result.time_window.start_iso.startswith("2024-04-29T18:00:00")
    assert result.time_window.end_iso.startswith("2024-04-29T21:00:00")
    assert result.time_window.tz == DEFAULT_TZ

    assert result.clarifier is not None
    assert result.clarifier.choices == ["Today", "Tomorrow", "This weekend"]


def test_friday_morning_resolves_to_next_friday():
    result = parse_time_request("Friday morning", reference_date=BASE_DATE)

    assert result.confidence == "high"
    assert result.time_window is not None
    assert result.time_window.start_iso.startswith("2024-05-03T09:00:00")
    assert result.time_window.end_iso.startswith("2024-05-03T12:00:00")
    assert result.clarifier is None


def test_weekend_window_with_single_clarifier():
    result = parse_time_request("on weekends", reference_date=BASE_DATE)

    assert result.confidence == "high"
    assert result.time_window is not None
    assert result.time_window.start_iso.startswith("2024-05-04T09:00:00")
    assert result.time_window.end_iso.startswith("2024-05-05T17:00:00")
    assert result.clarifier is not None
    assert len(result.clarifier.choices) == 3


def test_time_qualifier_respects_meridiem_and_day():
    result = parse_time_request("after 6am on friday", reference_date=BASE_DATE)

    assert result.confidence == "high"
    assert result.time_window is not None
    assert result.time_window.start_iso.startswith("2024-05-03T06:00:00")
    assert result.time_window.end_iso.startswith("2024-05-03T09:00:00")
    assert result.clarifier is None


def test_time_qualifier_crosses_midnight_when_needed():
    result = parse_time_request("after 11pm on friday", reference_date=BASE_DATE)

    assert result.confidence == "high"
    assert result.time_window is not None
    assert result.time_window.start_iso.startswith("2024-05-03T23:00:00")
    assert result.time_window.end_iso.startswith("2024-05-04T02:00:00")
    assert result.clarifier is None


def test_non_after_qualifier_is_ignored():
    result = parse_time_request("before 6", reference_date=BASE_DATE)

    assert result.confidence == "low"
    assert result.time_window is None
    assert result.clarifier is None
