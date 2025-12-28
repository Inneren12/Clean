ACTIVE = "ACTIVE"
PAUSED = "PAUSED"
CANCELED = "CANCELED"

STATUSES = {ACTIVE, PAUSED, CANCELED}

WEEKLY = "WEEKLY"
BIWEEKLY = "BIWEEKLY"
MONTHLY = "MONTHLY"

FREQUENCIES = {WEEKLY, BIWEEKLY, MONTHLY}


def normalize_status(value: str) -> str:
    upper = value.upper()
    if upper not in STATUSES:
        raise ValueError("Invalid subscription status")
    return upper


def normalize_frequency(value: str) -> str:
    upper = value.upper()
    if upper not in FREQUENCIES:
        raise ValueError("Invalid subscription frequency")
    return upper
