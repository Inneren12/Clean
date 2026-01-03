import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import redis.asyncio as redis
from fastapi import HTTPException, Request, status

from app.domain.bookings.schemas import SignedUrlResponse
from app.settings import settings


@dataclass
class PhotoTokenClaims:
    org_id: uuid.UUID
    order_id: str
    photo_id: str
    exp: int
    ua_hash: str | None = None
    variant: str | None = None


_redis_client: redis.Redis | None = None


def _photo_signing_secret() -> str:
    return (
        settings.photo_token_secret
        or settings.order_photo_signing_secret
        or settings.auth_secret_key
    )


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _hash_user_agent(user_agent: str | None) -> str:
    return hashlib.sha256((user_agent or "").encode()).hexdigest()


def normalize_variant(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if not re.match(r"^[A-Za-z0-9_.-]+$", candidate):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid variant")
    return candidate


def _encode_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    b64 = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    sig = hmac.new(_photo_signing_secret().encode(), raw, hashlib.sha256).hexdigest()
    return f"{b64}.{sig}"


def _decode_payload(token: str) -> tuple[dict[str, Any], str]:
    if "." not in token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    encoded, provided_sig = token.rsplit(".", 1)
    padding = "=" * (-len(encoded) % 4)
    try:
        raw = base64.urlsafe_b64decode(encoded + padding)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from exc
    expected = hmac.new(_photo_signing_secret().encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, provided_sig):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    try:
        payload = json.loads(raw.decode())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from exc
    return payload, provided_sig


def _parse_claims(payload: dict[str, Any]) -> PhotoTokenClaims:
    try:
        org_id = uuid.UUID(payload["org_id"])
        order_id = str(payload["order_id"]).strip()
        photo_id = str(payload["photo_id"]).strip()
        exp = int(payload["exp"])
        ua_hash = payload.get("ua")
        variant = payload.get("variant")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from exc

    if not order_id or not photo_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    return PhotoTokenClaims(
        org_id=org_id,
        order_id=order_id,
        photo_id=photo_id,
        exp=exp,
        ua_hash=ua_hash,
        variant=variant,
    )


def _ttl_seconds(exp: int) -> int:
    now_ts = int(time.time())
    remaining = exp - now_ts
    if remaining <= 0:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return remaining


def build_photo_download_token(
    *,
    org_id: uuid.UUID,
    order_id: str,
    photo_id: str,
    user_agent: str | None = None,
    ttl_seconds: int | None = None,
    variant: str | None = None,
) -> str:
    now = _now()
    ttl = ttl_seconds or settings.photo_url_ttl_seconds
    normalized_variant = normalize_variant(variant)
    exp = int((now + timedelta(seconds=ttl)).timestamp())
    payload: dict[str, Any] = {
        "org_id": str(org_id),
        "order_id": order_id,
        "photo_id": photo_id,
        "exp": exp,
        "typ": "photo_download",
        "jti": secrets.token_hex(8),
    }
    if normalized_variant:
        payload["variant"] = normalized_variant
    if settings.photo_token_bind_ua:
        payload["ua"] = _hash_user_agent(user_agent)
    return _encode_payload(payload)


async def _redis() -> redis.Redis | None:
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    if not settings.redis_url:
        return None
    _redis_client = redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=False)
    return _redis_client


async def _enforce_one_time(signature: str, ttl: int) -> None:
    if not settings.photo_token_one_time:
        return
    client = await _redis()
    if client is None:
        return
    key = f"photo-token:{signature}"
    try:
        created = await client.set(key, b"1", ex=max(ttl, 1), nx=True)
    except Exception:  # noqa: BLE001
        return
    if not created:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Download link already used")


async def verify_photo_download_token(token: str, *, user_agent: str | None = None) -> PhotoTokenClaims:
    payload, signature = _decode_payload(token)
    if payload.get("typ") != "photo_download":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    claims = _parse_claims(payload)
    if claims.ua_hash is not None:
        current_ua_hash = _hash_user_agent(user_agent)
        if not hmac.compare_digest(claims.ua_hash, current_ua_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    claims.variant = normalize_variant(claims.variant)
    ttl = _ttl_seconds(claims.exp)
    await _enforce_one_time(signature, ttl)
    return claims


async def build_signed_photo_response(
    photo,
    request: Request,
    storage,
    org_id: uuid.UUID,
    *,
    variant: str | None = None,
) -> SignedUrlResponse:
    ttl = settings.photo_url_ttl_seconds
    expires_at = _now() + timedelta(seconds=ttl)
    download_url = str(
        request.url_for(
            "signed_download_order_photo",
            order_id=photo.order_id,
            photo_id=photo.photo_id,
        )
    )
    token = build_photo_download_token(
        org_id=org_id,
        order_id=photo.order_id,
        photo_id=photo.photo_id,
        user_agent=request.headers.get("user-agent"),
        ttl_seconds=ttl,
        variant=variant,
    )
    download_url_with_token = f"{download_url}?token={token}"

    # Maintain compatibility with backends that need resource hints for downstream signing
    if hasattr(storage, "prepare_signed_download"):
        try:
            await storage.prepare_signed_download(key=photo.storage_key, resource_url=download_url_with_token)
        except Exception:
            pass

    return SignedUrlResponse(
        url=download_url_with_token, expires_at=expires_at, expires_in=ttl, variant=variant
    )
