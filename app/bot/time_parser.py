from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo


DEFAULT_TZ = "America/Edmonton"

_TIME_LABEL_WINDOWS: dict[str, tuple[time, time]] = {
    "morning": (time(9, 0), time(12, 0)),
    "afternoon": (time(12, 0), time(17, 0)),
    "evening": (time(17, 0), time(21, 0)),
}

_QUALIFIER_PATTERN = re.compile(
    r"\b(after|by|before)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b",
    flags=re.IGNORECASE,
)

_DAY_ALIASES: dict[str, int] = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


@dataclass
class Clarifier:
    question: str
    choices: List[str]


@dataclass
class NormalizedTimeWindow:
    start_iso: str
    end_iso: str
    tz: str


@dataclass
class TimeParseResult:
    time_window: Optional[NormalizedTimeWindow]
    confidence: str
    clarifier: Optional[Clarifier] = None


def _coerce_reference_date(reference: Optional[date], tz: str) -> date:
    if reference:
        return reference
    now = datetime.now(tz=ZoneInfo(tz))
    return now.date()


def _next_weekday(reference: date, target_weekday: int) -> date:
    delta = (target_weekday - reference.weekday()) % 7
    return reference + timedelta(days=delta)


def _resolve_day(normalized: str, reference: date) -> Optional[date]:
    for day_name, weekday_idx in _DAY_ALIASES.items():
        if day_name in normalized:
            return _next_weekday(reference, weekday_idx)
    return None


def _parse_time_component(match: re.Match[str]) -> time:
    qualifier, hour_str, minute_str, meridiem = match.groups()
    hour = int(hour_str)
    minute = int(minute_str) if minute_str else 0

    if meridiem:
        meridiem = meridiem.lower()
        if meridiem == "pm" and hour < 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
    elif qualifier.lower() == "after" and hour <= 6:
        # Without meridiem, prefer evening for common phrases like "after 6"
        hour = hour + 12 if hour < 12 else hour

    return time(hour=hour, minute=minute)


def _window_for_weekend(reference: date, tzinfo: ZoneInfo) -> NormalizedTimeWindow:
    days_to_saturday = (5 - reference.weekday()) % 7
    saturday = reference + timedelta(days=days_to_saturday)
    sunday = saturday + timedelta(days=1)

    start_dt = datetime.combine(saturday, time(9, 0), tzinfo)
    end_dt = datetime.combine(sunday, time(17, 0), tzinfo)

    return NormalizedTimeWindow(
        start_iso=start_dt.isoformat(), end_iso=end_dt.isoformat(), tz=str(tzinfo.key)
    )


def _build_window(
    target_date: date, start_time: time, end_time: time, tzinfo: ZoneInfo
) -> NormalizedTimeWindow:
    start_dt = datetime.combine(target_date, start_time, tzinfo)
    end_dt = datetime.combine(target_date, end_time, tzinfo)
    if end_dt <= start_dt:
        end_dt = end_dt + timedelta(hours=3)
    return NormalizedTimeWindow(
        start_iso=start_dt.isoformat(), end_iso=end_dt.isoformat(), tz=str(tzinfo.key)
    )


def parse_time_request(
    phrase: str, *, reference_date: Optional[date] = None, tz: str = DEFAULT_TZ
) -> TimeParseResult:
    normalized = phrase.lower().strip()
    tzinfo = ZoneInfo(tz)
    base_date = _coerce_reference_date(reference_date, tz)

    confidence = "low"
    clarifier: Optional[Clarifier] = None
    window: Optional[NormalizedTimeWindow] = None

    if not normalized:
        return TimeParseResult(time_window=None, confidence=confidence)

    weekend_requested = "weekend" in normalized or "weekends" in normalized
    day = _resolve_day(normalized, base_date)

    label = next((name for name in _TIME_LABEL_WINDOWS if name in normalized), None)
    qualifier_match = _QUALIFIER_PATTERN.search(normalized)

    if weekend_requested:
        window = _window_for_weekend(base_date, tzinfo)
        confidence = "high"
        clarifier = Clarifier(
            question="Which day works better this weekend?",
            choices=["Saturday morning", "Sunday morning", "Either is fine"],
        )
    elif label:
        start_time, end_time = _TIME_LABEL_WINDOWS[label]
        target_date = day or base_date
        window = _build_window(target_date, start_time, end_time, tzinfo)
        confidence = "high" if day else "medium"
        if not day:
            clarifier = Clarifier(
                question="Which day should I book for that time?",
                choices=["Today", "Tomorrow", "This weekend"],
            )
    elif qualifier_match:
        start_time = _parse_time_component(qualifier_match)
        target_date = day or base_date
        end_time = (datetime.combine(target_date, start_time, tzinfo) + timedelta(hours=3)).time()
        window = _build_window(target_date, start_time, end_time, tzinfo)
        confidence = "high" if day else "medium"
        if not day:
            clarifier = Clarifier(
                question="What day should we schedule after that time?",
                choices=["Today", "Tomorrow", "This weekend"],
            )

    return TimeParseResult(time_window=window, confidence=confidence, clarifier=clarifier)
