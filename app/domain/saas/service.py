from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.bookings.db_models import Team
from app.domain.bookings.service import DEFAULT_TEAM_NAME
from app.domain.saas.db_models import (
    ApiToken,
    Membership,
    MembershipRole,
    Organization,
    SaaSSession,
    TokenEvent,
    User,
)
from app.infra.auth import create_access_token, hash_api_token, hash_password, verify_password
from app.settings import settings

DEFAULT_ORG_NAME = "Default Org"


async def ensure_default_org_and_team(session: AsyncSession) -> tuple[Organization, Team]:
    """Ensure the deterministic default org/team exist for the current database.

    Uses dialect-specific upserts so it remains idempotent across Postgres (ON CONFLICT)
    and SQLite (INSERT OR IGNORE).
    """

    bind = session.get_bind()
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "") if bind else ""
    org_id = settings.default_org_id
    if dialect_name == "sqlite":
        org_stmt = sqlite.insert(Organization).values(org_id=org_id, name=DEFAULT_ORG_NAME)
        org_stmt = org_stmt.prefix_with("OR IGNORE")
        team_stmt = sqlite.insert(Team).values(team_id=1, org_id=org_id, name=DEFAULT_TEAM_NAME)
        team_stmt = team_stmt.prefix_with("OR IGNORE")
    else:
        org_stmt = (
            postgresql.insert(Organization)
            .values(org_id=org_id, name=DEFAULT_ORG_NAME)
            .on_conflict_do_nothing()
        )
        team_stmt = (
            postgresql.insert(Team)
            .values(team_id=1, org_id=org_id, name=DEFAULT_TEAM_NAME)
            .on_conflict_do_nothing()
        )

    await session.execute(org_stmt)
    await session.execute(team_stmt)

    org = await session.get(Organization, org_id)
    team = await session.get(Team, 1)
    if team and team.org_id != org_id:
        team.org_id = org_id
        session.add(team)
        await session.flush()

    if org is None:
        org = Organization(org_id=org_id, name=DEFAULT_ORG_NAME)
        session.add(org)
        await session.flush()

    if team is None:
        team = Team(team_id=1, org_id=org_id, name="Default Team")
        session.add(team)
        await session.flush()

    return org, team


async def create_organization(session: AsyncSession, name: str) -> Organization:
    org = Organization(name=name)
    session.add(org)
    await session.flush()
    return org


async def create_user(session: AsyncSession, email: str, password: str | None = None) -> User:
    password_hash = hash_password(password, settings=settings) if password else None
    user = User(email=email, password_hash=password_hash)
    session.add(user)
    await session.flush()
    return user


async def create_membership(
    session: AsyncSession,
    org: Organization,
    user: User,
    role: MembershipRole,
    is_active: bool = True,
) -> Membership:
    membership = Membership(org_id=org.org_id, user_id=user.user_id, role=role, is_active=is_active)
    session.add(membership)
    await session.flush()
    return membership


async def issue_service_token(
    session: AsyncSession,
    org: Organization,
    role: MembershipRole,
    description: str | None = None,
) -> tuple[str, ApiToken]:
    raw_token = secrets.token_urlsafe(32)
    token_hash = hash_api_token(raw_token)
    record = ApiToken(org_id=org.org_id, token_hash=token_hash, role=role, description=description)
    session.add(record)
    await session.flush()
    return raw_token, record


async def authenticate_user(
    session: AsyncSession, email: str, password: str, org_id: uuid.UUID | None
) -> tuple[User, Membership]:
    user = await session.scalar(sa.select(User).where(User.email == email))
    if not user or not user.is_active or not user.password_hash:
        raise ValueError("invalid_credentials")
    valid, upgraded = verify_password(password, user.password_hash, settings=settings)
    if not valid:
        raise ValueError("invalid_credentials")
    if upgraded and upgraded != user.password_hash:
        user.password_hash = upgraded
        session.add(user)
        await session.flush()

    membership_stmt = sa.select(Membership).where(Membership.user_id == user.user_id, Membership.is_active.is_(True))
    if org_id:
        membership_stmt = membership_stmt.where(Membership.org_id == org_id)
    membership = await session.scalar(membership_stmt)
    if not membership:
        raise ValueError("membership_not_found")
    return user, membership


def build_access_token(user: User, membership: Membership) -> str:
    return create_access_token(
        subject=str(user.user_id),
        org_id=str(membership.org_id),
        role=membership.role.value,
        ttl_minutes=settings.auth_access_token_ttl_minutes,
        settings=settings,
    )


def build_session_access_token(user: User, membership: Membership, session_id: uuid.UUID) -> str:
    return create_access_token(
        subject=str(user.user_id),
        org_id=str(membership.org_id),
        role=membership.role.value,
        ttl_minutes=settings.auth_access_token_ttl_minutes,
        settings=settings,
        session_id=session_id,
        token_id=uuid.uuid4(),
    )


async def create_session(
    session: AsyncSession,
    user: User,
    membership: Membership,
    *,
    ttl_minutes: int,
    refresh_ttl_minutes: int,
    request_id: str | None = None,
) -> tuple[SaaSSession, str]:
    now = datetime.now(timezone.utc)
    refresh_token = secrets.token_urlsafe(48)
    record = SaaSSession(
        session_id=uuid.uuid4(),
        user_id=user.user_id,
        org_id=membership.org_id,
        role=membership.role,
        refresh_token_hash=hash_api_token(refresh_token),
        created_at=now,
        expires_at=now + timedelta(minutes=ttl_minutes),
        refresh_expires_at=now + timedelta(minutes=refresh_ttl_minutes),
    )
    session.add(record)
    await session.flush()
    await record_audit_event(
        session,
        record,
        user,
        event_type="issued",
        token_type="refresh",
        request_id=request_id,
    )
    return record, refresh_token


async def rotate_session(
    session: AsyncSession,
    prior: SaaSSession,
    *,
    ttl_minutes: int,
    refresh_ttl_minutes: int,
    request_id: str | None = None,
) -> tuple[SaaSSession, str]:
    prior.revoked_at = datetime.now(timezone.utc)
    prior.revoked_reason = "rotated"
    session.add(prior)
    await session.flush()
    user = await session.get(User, prior.user_id)
    membership = await session.scalar(
        sa.select(Membership).where(Membership.user_id == prior.user_id, Membership.org_id == prior.org_id)
    )
    if not user or not membership:
        raise ValueError("invalid_session_state")
    new_session, refresh_token = await create_session(
        session,
        user,
        membership,
        ttl_minutes=ttl_minutes,
        refresh_ttl_minutes=refresh_ttl_minutes,
        request_id=request_id,
    )
    new_session.rotated_from = prior.session_id
    session.add(new_session)
    await session.flush()
    await record_audit_event(
        session,
        new_session,
        user,
        event_type="refreshed",
        token_type="refresh",
        request_id=request_id,
    )
    return new_session, refresh_token


async def refresh_tokens(
    session: AsyncSession, refresh_token: str, *, request_id: str | None = None
) -> tuple[str, str, SaaSSession, Membership]:
    hashed = hash_api_token(refresh_token)
    token_session = await session.scalar(
        sa.select(SaaSSession).where(SaaSSession.refresh_token_hash == hashed).order_by(SaaSSession.created_at.desc())
    )
    if not token_session:
        raise ValueError("invalid_refresh")
    now = datetime.now(timezone.utc)
    revoked_at = token_session.revoked_at
    if revoked_at and revoked_at.tzinfo is None:
        revoked_at = revoked_at.replace(tzinfo=timezone.utc)
    refresh_expires_at = token_session.refresh_expires_at
    if refresh_expires_at.tzinfo is None:
        refresh_expires_at = refresh_expires_at.replace(tzinfo=timezone.utc)
    if revoked_at:
        raise ValueError("revoked")
    if refresh_expires_at < now:
        raise ValueError("expired")

    user = await session.get(User, token_session.user_id)
    membership = await session.scalar(
        sa.select(Membership).where(Membership.user_id == token_session.user_id, Membership.org_id == token_session.org_id)
    )
    if not user or not membership:
        raise ValueError("invalid_refresh_state")

    new_session, new_refresh = await rotate_session(
        session,
        token_session,
        ttl_minutes=settings.auth_session_ttl_minutes,
        refresh_ttl_minutes=settings.auth_refresh_token_ttl_minutes,
        request_id=request_id,
    )
    access_token = build_session_access_token(user, membership, new_session.session_id)
    return access_token, new_refresh, new_session, membership


async def revoke_session(
    session: AsyncSession, session_id: uuid.UUID, *, reason: str = "revoked", request_id: str | None = None
) -> None:
    record = await session.get(SaaSSession, session_id)
    if not record:
        return
    record.revoked_at = datetime.now(timezone.utc)
    record.revoked_reason = reason
    session.add(record)
    user = await session.get(User, record.user_id)
    if user:
        await record_audit_event(
            session,
            record,
            user,
            event_type="revoked",
            token_type="refresh",
            request_id=request_id,
        )


async def revoke_user_sessions(session: AsyncSession, user_id: uuid.UUID, *, reason: str = "revoked") -> None:
    now = datetime.now(timezone.utc)
    await session.execute(
        sa.update(SaaSSession)
        .where(SaaSSession.user_id == user_id, SaaSSession.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason=reason)
    )


async def validate_session_record(session: AsyncSession, session_id: uuid.UUID) -> SaaSSession | None:
    record = await session.get(SaaSSession, session_id)
    if not record:
        return None
    now = datetime.now(timezone.utc)
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    revoked_at = record.revoked_at
    if revoked_at and revoked_at.tzinfo is None:
        revoked_at = revoked_at.replace(tzinfo=timezone.utc)
    if revoked_at or expires_at < now:
        return None
    return record


async def record_audit_event(
    session: AsyncSession,
    token_session: SaaSSession,
    user: User,
    *,
    event_type: str,
    token_type: str,
    request_id: str | None = None,
    details: dict | None = None,
) -> TokenEvent:
    event = TokenEvent(
        session_id=token_session.session_id,
        user_id=user.user_id,
        org_id=token_session.org_id,
        event_type=event_type,
        token_type=token_type,
        request_id=request_id,
        actor_role=token_session.role,
        details=details or {},
    )
    session.add(event)
    await session.flush()
    return event
