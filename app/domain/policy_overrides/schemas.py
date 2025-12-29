from enum import StrEnum


class OverrideType(StrEnum):
    DEPOSIT_REQUIRED = "deposit_required"
    DEPOSIT_AMOUNT = "deposit_amount"
    RISK_BAND = "risk_band"
    CANCELLATION_POLICY = "cancellation_policy"
    CANCELLATION_EXCEPTION = "cancellation_exception"
