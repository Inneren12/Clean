from __future__ import annotations

from typing import Final, Iterable

LEAD_STATUS_NEW: Final[str] = "NEW"
LEAD_STATUS_CONTACTED: Final[str] = "CONTACTED"
LEAD_STATUS_BOOKED: Final[str] = "BOOKED"
LEAD_STATUS_DONE: Final[str] = "DONE"
LEAD_STATUS_CANCELLED: Final[str] = "CANCELLED"

LEAD_STATUSES: Final[tuple[str, ...]] = (
    LEAD_STATUS_NEW,
    LEAD_STATUS_CONTACTED,
    LEAD_STATUS_BOOKED,
    LEAD_STATUS_DONE,
    LEAD_STATUS_CANCELLED,
)

_ALLOWED_TRANSITIONS: Final[dict[str, set[str]]] = {
    LEAD_STATUS_NEW: {LEAD_STATUS_CONTACTED, LEAD_STATUS_BOOKED, LEAD_STATUS_CANCELLED},
    LEAD_STATUS_CONTACTED: {LEAD_STATUS_BOOKED, LEAD_STATUS_CANCELLED},
    LEAD_STATUS_BOOKED: {LEAD_STATUS_DONE, LEAD_STATUS_CANCELLED},
    LEAD_STATUS_DONE: set(),
    LEAD_STATUS_CANCELLED: set(),
}


def is_valid_status(value: str) -> bool:
    return value in LEAD_STATUSES


def allowed_next_statuses(current: str) -> set[str]:
    return _ALLOWED_TRANSITIONS.get(current, set())


def assert_valid_transition(current: str, target: str) -> None:
    if not is_valid_status(target):
        raise ValueError(f"Unknown lead status: {target}")
    if current == target:
        return
    allowed = allowed_next_statuses(current)
    if not allowed:
        raise ValueError(f"Lead is already in terminal status: {current}")
    if target not in allowed:
        raise ValueError(f"Cannot transition lead from {current} to {target}")


def default_lead_status() -> str:
    return LEAD_STATUS_NEW


def statuses_for_filter() -> Iterable[str]:
    return LEAD_STATUSES
