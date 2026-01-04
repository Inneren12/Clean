from __future__ import annotations

import base64
import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Literal

import bcrypt
import jwt
from argon2 import PasswordHasher as Argon2Hasher
from argon2.exceptions import VerifyMismatchError


LEGACY_SHA256_PREFIX = "sha256$"
ARGON2_PREFIX = "argon2id$"
BCRYPT_PREFIX = "bcrypt$"


@dataclass
class PasswordHasher:
    default_scheme: Literal["argon2id", "bcrypt"] = "argon2id"
    argon2_time_cost: int = 3
    argon2_memory_cost: int = 65536
    argon2_parallelism: int = 2
    bcrypt_cost: int = 12

    def __post_init__(self) -> None:
        self._argon2 = Argon2Hasher(
            time_cost=self.argon2_time_cost,
            memory_cost=self.argon2_memory_cost,
            parallelism=self.argon2_parallelism,
            hash_len=32,
            salt_len=16,
        )

    def hash(self, password: str | None) -> str:
        if password is None:
            return ""
        if self.default_scheme == "bcrypt":
            return self._hash_bcrypt(password)
        return self._hash_argon2(password)

    def verify(self, password: str, stored_hash: str) -> tuple[bool, str | None]:
        if not stored_hash:
            return False, None

        if stored_hash.startswith(ARGON2_PREFIX):
            return self._verify_argon2(password, stored_hash)
        if stored_hash.startswith(BCRYPT_PREFIX):
            return self._verify_bcrypt(password, stored_hash)
        return self._verify_legacy_sha256(password, stored_hash)

    def _hash_argon2(self, password: str) -> str:
        raw = self._argon2.hash(password).lstrip("$")
        return f"{ARGON2_PREFIX}{raw}"

    def _hash_bcrypt(self, password: str) -> str:
        salt = bcrypt.gensalt(rounds=self.bcrypt_cost)
        digest = bcrypt.hashpw(password.encode(), salt).decode().lstrip("$")
        return f"{BCRYPT_PREFIX}{digest}"

    def _verify_argon2(self, password: str, stored_hash: str) -> tuple[bool, str | None]:
        encoded = stored_hash.removeprefix(ARGON2_PREFIX)
        if not encoded.startswith("$"):
            encoded = f"${encoded}"
        try:
            self._argon2.verify(encoded, password)
            if self._argon2.check_needs_rehash(encoded):
                return True, self._hash_argon2(password)
            return True, None
        except VerifyMismatchError:
            return False, None

    def _verify_bcrypt(self, password: str, stored_hash: str) -> tuple[bool, str | None]:
        encoded = stored_hash.removeprefix(BCRYPT_PREFIX)
        if not encoded.startswith("$"):
            encoded = f"${encoded}"
        try:
            valid = bcrypt.checkpw(password.encode(), encoded.encode())
        except ValueError:
            return False, None
        if not valid:
            return False, None
        try:
            rounds = bcrypt.gensalt(rounds=self.bcrypt_cost).decode().split("$")[2]
            current_rounds = encoded.split("$")[2]
            if rounds != current_rounds:
                return True, self._hash_bcrypt(password)
        except Exception:
            pass
        return True, None

    def _verify_legacy_sha256(self, password: str, stored_hash: str) -> tuple[bool, str | None]:
        try:
            salt, digest = stored_hash.removeprefix(LEGACY_SHA256_PREFIX).split("$", 1)
        except ValueError:
            return False, None
        candidate = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
        if not secrets.compare_digest(candidate, digest):
            return False, None
        upgraded = self.hash(password)
        return True, upgraded


def password_hasher_from_settings(settings) -> PasswordHasher:
    return PasswordHasher(
        default_scheme=getattr(settings, "password_hash_scheme", "argon2id"),
        argon2_time_cost=getattr(settings, "password_hash_argon2_time_cost", 3),
        argon2_memory_cost=getattr(settings, "password_hash_argon2_memory_cost", 65536),
        argon2_parallelism=getattr(settings, "password_hash_argon2_parallelism", 2),
        bcrypt_cost=getattr(settings, "password_hash_bcrypt_cost", 12),
    )


def hash_password(password: str | None, *, settings=None) -> str:
    hasher = password_hasher_from_settings(settings) if settings else PasswordHasher()
    return hasher.hash(password)


def verify_password(password: str, stored_hash: str, *, settings=None) -> tuple[bool, str | None]:
    hasher = password_hasher_from_settings(settings) if settings else PasswordHasher()
    return hasher.verify(password, stored_hash)


def create_access_token(
    subject: str,
    org_id: str,
    role: str,
    ttl_minutes: int,
    settings,
    *,
    session_id: uuid.UUID | None = None,
    token_id: uuid.UUID | None = None,
    mfa_verified: bool | None = None,
) -> str:
    expire = datetime.now(tz=timezone.utc) + timedelta(minutes=ttl_minutes)
    payload: Dict[str, Any] = {
        "sub": subject,
        "org_id": org_id,
        "role": role,
        "exp": expire,
        "iat": datetime.now(tz=timezone.utc),
    }
    if session_id:
        payload["sid"] = str(session_id)
    if token_id:
        payload["jti"] = str(token_id)
    if mfa_verified:
        payload["mfa"] = True
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
