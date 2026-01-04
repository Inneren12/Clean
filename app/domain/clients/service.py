import base64
import hashlib
import hmac
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.clients.db_models import ClientUser
from app.settings import settings

logger = logging.getLogger(__name__)


def _b64_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


@dataclass
class TokenResult:
    email: str
    client_id: str
    issued_at: datetime
    expires_at: datetime
    org_id: uuid.UUID


def issue_magic_token(
    email: str,
    client_id: str,
    *,
    secret: str,
    ttl_minutes: int,
    issued_at: datetime | None = None,
    org_id: uuid.UUID | None = None,
) -> str:
    now = issued_at or datetime.now(timezone.utc)
    org = org_id or settings.default_org_id
    payload = {
        "email": email.lower(),
        "client_id": client_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl_minutes)).timestamp()),
        "org_id": str(org),
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return f"{_b64_encode(body)}.{_b64_encode(signature)}"


def verify_magic_token(token: str, *, secret: str) -> TokenResult:
    try:
        body_b64, sig_b64 = token.split(".", 1)
    except ValueError as exc:
        raise ValueError("invalid_token_format") from exc

    body = _b64_decode(body_b64)
    expected_sig = _b64_encode(hmac.new(secret.encode(), body, hashlib.sha256).digest())
    if not hmac.compare_digest(expected_sig, sig_b64):
        raise ValueError("invalid_token_signature")

    payload = json.loads(body.decode())
    issued_at = datetime.fromtimestamp(payload.get("iat", 0), tz=timezone.utc)
    expires_at = datetime.fromtimestamp(payload.get("exp", 0), tz=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise ValueError("token_expired")

    try:
        org_id = uuid.UUID(payload.get("org_id"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("token_invalid_org") from exc

    return TokenResult(
        email=payload["email"],
        client_id=payload["client_id"],
        issued_at=issued_at,
        expires_at=expires_at,
        org_id=org_id,
    )


async def get_or_create_client(
    session: AsyncSession, email: str, name: str | None = None, commit: bool = True
) -> ClientUser:
    normalized = email.lower().strip()
    stmt = select(ClientUser).where(func.lower(ClientUser.email) == normalized)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if user:
        return user

    user = ClientUser(email=normalized, name=name)
    session.add(user)
    if commit:
        await session.commit()
        await session.refresh(user)
    else:
        await session.flush()
    return user


async def attach_client_to_orders(session: AsyncSession, client: ClientUser) -> None:
    from app.domain.bookings.db_models import Booking  # Imported lazily to avoid cycle
    from app.domain.leads.db_models import Lead

    stmt = (
        update(Booking)
        .where(Booking.client_id.is_(None))
        .where(Booking.lead_id.is_not(None))
        .where(
            Booking.lead_id.in_(
                select(Lead.lead_id).where(func.lower(Lead.email) == client.email.lower())
            )
        )
        .values(client_id=client.client_id)
    )
    await session.execute(stmt)
    await session.commit()
