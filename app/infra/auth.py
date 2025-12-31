from __future__ import annotations

import base64
import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import jwt


def hash_password(password: str | None) -> str:
    if password is None:
        return ""
    salt = secrets.token_hex(8)
    digest = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False
    try:
        salt, digest = stored_hash.split("$", 1)
    except ValueError:
        return False
    candidate = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return secrets.compare_digest(candidate, digest)


def create_access_token(
    subject: str,
    org_id: str,
    role: str,
    ttl_minutes: int,
    settings,
) -> str:
    expire = datetime.now(tz=timezone.utc) + timedelta(minutes=ttl_minutes)
    payload: Dict[str, Any] = {
        "sub": subject,
        "org_id": org_id,
        "role": role,
        "exp": expire,
        "iat": datetime.now(tz=timezone.utc),
    }
    return jwt.encode(payload, settings.auth_secret_key, algorithm="HS256")


def decode_access_token(token: str, secret: str) -> dict[str, Any]:
    return jwt.decode(token, secret, algorithms=["HS256"])


def hash_api_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def build_bearer_token(raw: str) -> str:
    return base64.b64encode(raw.encode()).decode()


def is_token_expired(token: str, secret: str) -> bool:
    try:
        decoded = decode_access_token(token, secret)
    except jwt.ExpiredSignatureError:
        return True
    except jwt.InvalidTokenError:
        return True
    exp = decoded.get("exp")
    if isinstance(exp, (int, float)):
        return exp < time.time()
    if isinstance(exp, datetime):
        return exp < datetime.now(tz=timezone.utc)
    return False
