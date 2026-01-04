import secrets
from datetime import datetime, timedelta, timezone
from typing import Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.break_glass.db_models import BreakGlassSession
from app.infra.auth import hash_api_token

DEFAULT_TTL_MINUTES = 30
MAX_TTL_MINUTES = 240


def _normalize_ttl(ttl_minutes: int | None) -> int:
    if ttl_minutes is None:
        return DEFAULT_TTL_MINUTES
    return max(1, min(ttl_minutes, MAX_TTL_MINUTES))


async def create_session(
    session: AsyncSession,
    *,
    org_id,
    actor: str,
    reason: str,
    ttl_minutes: int | None,
) -> Tuple[str, BreakGlassSession]:
    ttl = _normalize_ttl(ttl_minutes)
    token = secrets.token_urlsafe(32)
    token_hash = hash_api_token(token)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl)

    record = BreakGlassSession(
        org_id=org_id,
        actor=actor,
        reason=reason,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    session.add(record)
    return token, record


async def get_valid_session(
    session: AsyncSession, *, org_id, token: str
) -> BreakGlassSession | None:
    token_hash = hash_api_token(token)
    result = await session.execute(
        select(BreakGlassSession).where(
            BreakGlassSession.org_id == org_id, BreakGlassSession.token_hash == token_hash
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        return None

    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at <= datetime.now(timezone.utc):
        return None

    return record
