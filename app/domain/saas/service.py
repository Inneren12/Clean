from __future__ import annotations

import secrets
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.saas.db_models import ApiToken, Membership, MembershipRole, Organization, User
from app.infra.auth import create_access_token, hash_password, verify_password
from app.settings import settings


async def create_organization(session: AsyncSession, name: str) -> Organization:
    org = Organization(name=name)
    session.add(org)
    await session.flush()
    return org


async def create_user(session: AsyncSession, email: str, password: str | None = None) -> User:
    password_hash = hash_password(password) if password else None
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
    token_hash = hash_password(raw_token)
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
    if not verify_password(password, user.password_hash):
        raise ValueError("invalid_credentials")

    membership_stmt = sa.select(Membership).where(Membership.user_id == user.user_id, Membership.is_active.is_(True))
    if org_id:
        membership_stmt = membership_stmt.where(Membership.org_id == org_id)
    membership = await session.scalar(membership_stmt)
    if not membership:
        raise ValueError("membership_not_found")
    return user, membership


def build_access_token(user: User, membership: Membership) -> str:
    expires_minutes = settings.auth_token_ttl_minutes
    return create_access_token(
        subject=str(user.user_id),
        org_id=str(membership.org_id),
        role=membership.role.value,
        ttl_minutes=expires_minutes,
        settings=settings,
    )
