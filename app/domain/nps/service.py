import base64
import hashlib
import hmac
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.bookings.db_models import Booking
from app.domain.clients.db_models import ClientUser
from app.domain.nps.db_models import NpsResponse, SupportTicket

logger = logging.getLogger(__name__)

TICKET_STATUSES = {"OPEN", "IN_PROGRESS", "RESOLVED"}


@dataclass
class NpsTokenResult:
    order_id: str
    client_id: str | None
    email: str | None
    issued_at: datetime
    expires_at: datetime


def _b64_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def issue_nps_token(
    order_id: str,
    *,
    client_id: str | None,
    email: str | None,
    secret: str,
    ttl_days: int = 30,
    issued_at: datetime | None = None,
) -> str:
    now = issued_at or datetime.now(timezone.utc)
    payload = {
        "order_id": order_id,
        "client_id": client_id,
        "email": (email or "").lower(),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=ttl_days)).timestamp()),
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return f"{_b64_encode(body)}.{_b64_encode(signature)}"


def verify_nps_token(token: str, *, secret: str) -> NpsTokenResult:
    try:
        body_b64, sig_b64 = token.split(".", 1)
    except ValueError as exc:  # noqa: B904
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

    return NpsTokenResult(
        order_id=payload["order_id"],
        client_id=payload.get("client_id") or None,
        email=(payload.get("email") or None),
        issued_at=issued_at,
        expires_at=expires_at,
    )


async def get_existing_response(session: AsyncSession, order_id: str) -> NpsResponse | None:
    stmt = select(NpsResponse).where(NpsResponse.order_id == order_id).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def record_response(
    session: AsyncSession,
    *,
    booking: Booking,
    score: int,
    comment: str | None,
) -> NpsResponse:
    existing = await get_existing_response(session, booking.booking_id)
    if existing:
        return existing

    response = NpsResponse(
        order_id=booking.booking_id,
        client_id=booking.client_id,
        score=score,
        comment=comment,
    )
    session.add(response)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        existing = await get_existing_response(session, booking.booking_id)
        if existing:
            return existing
        raise
    logger.info(
        "nps_submitted",
        extra={
            "extra": {
                "order_id": booking.booking_id,
                "client_id": booking.client_id,
                "score": score,
            }
        },
    )
    return response


async def get_existing_ticket(session: AsyncSession, order_id: str) -> SupportTicket | None:
    stmt = select(SupportTicket).where(SupportTicket.order_id == order_id).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def ensure_ticket_for_low_score(
    session: AsyncSession,
    *,
    booking: Booking,
    score: int,
    comment: str | None,
    client: ClientUser | None,
) -> SupportTicket | None:
    existing = await get_existing_ticket(session, booking.booking_id)
    if existing:
        return existing

    subject = f"Low NPS for order {booking.booking_id}"
    body_parts = [
        f"Score: {score}",
        f"Order ID: {booking.booking_id}",
    ]
    if client and client.email:
        body_parts.append(f"Client email: {client.email}")
    if comment:
        body_parts.append("Comment: " + comment)
    body = "\n".join(body_parts)

    ticket = SupportTicket(
        order_id=booking.booking_id,
        client_id=booking.client_id,
        status="OPEN",
        priority="high" if score <= 1 else "normal",
        subject=subject,
        body=body,
    )
    session.add(ticket)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        existing_ticket = await get_existing_ticket(session, booking.booking_id)
        if existing_ticket:
            return existing_ticket
        raise
    logger.info(
        "ticket_created_from_nps",
        extra={
            "extra": {
                "order_id": booking.booking_id,
                "client_id": booking.client_id,
                "score": score,
            }
        },
    )
    return ticket


async def list_tickets(
    session: AsyncSession,
    *,
    org_id: uuid.UUID | None = None,
    status_filter: str | None = None,
    priority_filter: str | None = None,
    order_id: str | None = None,
) -> list[SupportTicket]:
    stmt = (
        select(SupportTicket)
        .join(Booking, SupportTicket.order_id == Booking.booking_id)
        .order_by(SupportTicket.created_at.desc())
    )
    if org_id:
        stmt = stmt.where(Booking.org_id == org_id)
    if status_filter:
        if status_filter not in TICKET_STATUSES:
            raise ValueError("invalid_status")
        stmt = stmt.where(SupportTicket.status == status_filter)
    if priority_filter:
        stmt = stmt.where(SupportTicket.priority == priority_filter)
    if order_id:
        stmt = stmt.where(SupportTicket.order_id == order_id)

    result = await session.execute(stmt)
    return list(result.scalars().all())


async def update_ticket_status(
    session: AsyncSession, ticket_id: str, status: str, *, org_id: uuid.UUID | None = None
) -> SupportTicket | None:
    if status not in TICKET_STATUSES:
        raise ValueError("invalid_status")

    stmt = select(SupportTicket).join(Booking, SupportTicket.order_id == Booking.booking_id)
    if org_id:
        stmt = stmt.where(Booking.org_id == org_id)
    stmt = stmt.where(SupportTicket.id == ticket_id)
    result = await session.execute(stmt)
    ticket = result.scalar_one_or_none()
    if ticket is None:
        return None
    ticket.status = status
    await session.flush()
    return ticket
