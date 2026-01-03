import contextvars
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
AUTH_HEADER_RE = re.compile(r"(?i)\bauthorization\s*[:=]\s*[^\s]+")
TOKEN_QUERY_RE = re.compile(
    r"(?P<key>(?:token|access_token|refresh_token|auth|signature|sig|x-amz-signature|x-amz-credential|x-amz-security-token))="
    r"(?P<value>[^&\s]+)",
    re.IGNORECASE,
)
BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+")
PII_KEYS = {"phone", "email", "address"}
SENSITIVE_KEYS = PII_KEYS | {
    "authorization",
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "signed_url",
    "signature",
    "sig",
}
LOG_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar("log_context", default={})
_LOG_RECORD_ATTRS = set(logging.LogRecord(None, 0, "", 0, "", (), None).__dict__.keys())


def redact_pii(value: str) -> str:
    value = EMAIL_RE.sub("[REDACTED_EMAIL]", value)
    value = PHONE_RE.sub("[REDACTED_PHONE]", value)
    value = ADDRESS_RE.sub("[REDACTED_ADDRESS]", value)
    value = TOKEN_QUERY_RE.sub(lambda match: f"{match.group('key')}=[REDACTED_TOKEN]", value)
    value = AUTH_HEADER_RE.sub("authorization=[REDACTED_TOKEN]", value)
    value = BEARER_RE.sub("Bearer [REDACTED_TOKEN]", value)
    return value


def _sanitize_value(value: Any, key: str | None = None) -> Any:
    if key and key.lower() in SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, str):
        return redact_pii(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, dict):
        return {item_key: _sanitize_value(item_value, item_key) for item_key, item_value in value.items()}
    return value


def update_log_context(**kwargs: Any) -> dict[str, Any]:
    current = LOG_CONTEXT.get({})
    sanitized = {key: value for key, value in kwargs.items() if value is not None}
    merged = {**current, **sanitized}
    LOG_CONTEXT.set(merged)
    return merged


def clear_log_context() -> None:
    LOG_CONTEXT.set({})


def _extract_extra(record: logging.LogRecord) -> dict[str, Any]:
    structured: dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key in _LOG_RECORD_ATTRS or key.startswith("_"):
            continue
        structured[key] = value
    extra_payload = structured.pop("extra", None)
    if isinstance(extra_payload, dict):
        structured.update(extra_payload)
    return structured


class RedactingJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: D401 - concise formatter
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": redact_pii(str(record.getMessage())),
            "logger": record.name,
        }
        context = LOG_CONTEXT.get({})
        if context:
            payload.update(_sanitize_value(context))
        extra = _extract_extra(record)
        if extra:
            payload.update(_sanitize_value(extra))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingJsonFormatter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(handler)
