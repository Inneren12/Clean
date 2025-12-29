from enum import StrEnum


class OverrideType(StrEnum):
    RISK_BAND = "risk_band"
    DEPOSIT = "deposit_override"
    CANCELLATION_EXCEPTION = "cancellation_exception"
