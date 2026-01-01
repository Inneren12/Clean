import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import jwt
from fastapi import HTTPException, status

from app.domain.bookings import photos_service
from app.domain.bookings.schemas import SignedUrlResponse
from app.settings import settings


def _append_query(url: str, **params: str) -> str:
    parsed = urlparse(url)
    existing_query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    merged = {**existing_query, **params}
    new_query = urlencode(merged, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def build_photo_download_token(*, org_id: uuid.UUID, order_id: str, photo_id: str) -> str:
    now = datetime.now(tz=timezone.utc)
    payload = {
        "org_id": str(org_id),
        "order_id": order_id,
        "photo_id": photo_id,
        "typ": "photo_download",
        "jti": secrets.token_hex(8),
        "iat": now,
        "exp": now + timedelta(seconds=settings.order_photo_signed_url_ttl_seconds),
    }
    return jwt.encode(payload, settings.auth_secret_key, algorithm="HS256")


def verify_photo_download_token(token: str) -> Tuple[uuid.UUID, str, str]:
    try:
        payload = jwt.decode(token, settings.auth_secret_key, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from exc

    try:
        typ = payload["typ"]
        if typ != "photo_download":
            raise KeyError("Invalid token type")

        org_id = uuid.UUID(payload["org_id"])
        order_id = payload["order_id"]
        photo_id = payload["photo_id"]
        if not isinstance(order_id, str) or not isinstance(photo_id, str):
            raise TypeError("Invalid token payload")
        order_id = order_id.strip()
        photo_id = photo_id.strip()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from exc

    if not order_id or not photo_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    return org_id, order_id, photo_id


async def build_signed_photo_response(photo, request, storage, org_id: uuid.UUID) -> SignedUrlResponse:
    ttl = settings.order_photo_signed_url_ttl_seconds
    expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=ttl)
    download_url = str(
        request.url_for(
            "signed_download_order_photo",
            order_id=photo.order_id,
            photo_id=photo.photo_id,
        )
    )
    token = build_photo_download_token(org_id=org_id, order_id=photo.order_id, photo_id=photo.photo_id)
    download_url_with_token = _append_query(download_url, token=token)

    key = photos_service.storage_key_for_photo(photo, org_id)
    signed_url = await storage.generate_signed_get_url(
        key=key,
        expires_in=ttl,
        resource_url=download_url_with_token,
    )

    return SignedUrlResponse(url=signed_url, expires_at=expires_at)
