import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.\-\s]+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|"
    r"Lane|Ln|Way|Court|Ct|Crescent|Cres|Trail|Trl|Place|Pl)\b",
    re.IGNORECASE,
)
PII_KEYS = {"phone", "email", "address"}


def redact_pii(value: str) -> str:
    value = EMAIL_RE.sub("[REDACTED_EMAIL]", value)
    value = PHONE_RE.sub("[REDACTED_PHONE]", value)
    value = ADDRESS_RE.sub("[REDACTED_ADDRESS]", value)
    return value


def _sanitize_value(value: Any, key: str | None = None) -> Any:
    if key and key.lower() in PII_KEYS:
        return "[REDACTED]"
    if isinstance(value, str):
        return redact_pii(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, dict):
        return {item_key: _sanitize_value(item_value, item_key) for item_key, item_value in value.items()}
    return value


class RedactingJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": redact_pii(str(record.getMessage())),
            "logger": record.name,
        }
        if hasattr(record, "extra") and isinstance(record.extra, dict):
            payload.update(_sanitize_value(record.extra))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingJsonFormatter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(handler)
