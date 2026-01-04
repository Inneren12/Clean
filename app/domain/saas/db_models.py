from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infra.db import Base, UUID_TYPE


class MembershipRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    DISPATCHER = "dispatcher"
    FINANCE = "finance"
    VIEWER = "viewer"
    WORKER = "worker"


class Organization(Base):
    __tablename__ = "organizations"

    org_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False
    )

    memberships: Mapped[list["Membership"]] = relationship("Membership", back_populates="organization")
    api_tokens: Mapped[list["ApiToken"]] = relationship("ApiToken", back_populates="organization")


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(sa.String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    must_change_password: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    password_changed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    temp_password_issued_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, server_default=sa.true())
    totp_secret_base32: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(sa.Boolean, default=False, server_default=sa.false())
    totp_enrolled_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    memberships: Mapped[list["Membership"]] = relationship("Membership", back_populates="user")


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        sa.UniqueConstraint("org_id", "user_id", name="uq_memberships_org_user"),
    )

    membership_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, sa.ForeignKey("organizations.org_id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, sa.ForeignKey("users.user_id", ondelete="CASCADE")
    )
    role: Mapped[MembershipRole] = mapped_column(
        sa.Enum(MembershipRole, name="membershiprole"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, server_default=sa.true())
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    organization: Mapped[Organization] = relationship("Organization", back_populates="memberships")
    user: Mapped[User] = relationship("User", back_populates="memberships")


class ApiToken(Base):
    __tablename__ = "api_tokens"
    __table_args__ = (
        sa.UniqueConstraint("token_hash", name="uq_api_tokens_hash"),
    )

    token_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, sa.ForeignKey("organizations.org_id", ondelete="CASCADE")
    )
    token_hash: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    role: Mapped[MembershipRole] = mapped_column(
        sa.Enum(MembershipRole, name="membershiprole"), nullable=False
    )
    description: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    organization: Mapped[Organization] = relationship("Organization", back_populates="api_tokens")


class SaaSSession(Base):
    __tablename__ = "saas_sessions"

    session_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, sa.ForeignKey("users.user_id", ondelete="CASCADE"))
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, sa.ForeignKey("organizations.org_id", ondelete="CASCADE")
    )
    role: Mapped[MembershipRole] = mapped_column(sa.Enum(MembershipRole, name="membershiprole"), nullable=False)
    refresh_token_hash: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    refresh_expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    rotated_from: Mapped[uuid.UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    mfa_verified: Mapped[bool] = mapped_column(sa.Boolean, default=False, server_default=sa.false())

    user: Mapped[User] = relationship("User")
    organization: Mapped[Organization] = relationship("Organization")


class TokenEvent(Base):
    __tablename__ = "token_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, sa.ForeignKey("saas_sessions.session_id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, sa.ForeignKey("users.user_id", ondelete="CASCADE"))
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, sa.ForeignKey("organizations.org_id", ondelete="CASCADE")
    )
    event_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    token_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    actor_role: Mapped[MembershipRole] = mapped_column(
        sa.Enum(MembershipRole, name="membershiprole"), nullable=False
    )
    request_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    details: Mapped[dict] = mapped_column("metadata", sa.JSON(), default=dict, server_default=sa.text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


class PasswordResetEvent(Base):
    __tablename__ = "password_reset_events"

    event_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, sa.ForeignKey("organizations.org_id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, sa.ForeignKey("users.user_id", ondelete="CASCADE")
    )
    actor_admin: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    reason: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


class OrganizationBilling(Base):
    __tablename__ = "organization_billing"
    __table_args__ = (sa.UniqueConstraint("org_id", name="uq_org_billing_org"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, sa.ForeignKey("organizations.org_id", ondelete="CASCADE")
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    plan_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="inactive", server_default="inactive")
    current_period_end: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    pause_reason_code: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    resume_reason_code: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    resumed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    organization: Mapped[Organization] = relationship("Organization")


class OrganizationUsageEvent(Base):
    __tablename__ = "organization_usage_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, sa.ForeignKey("organizations.org_id", ondelete="CASCADE")
    )
    metric: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    quantity: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1, server_default="1")
    resource_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    organization: Mapped[Organization] = relationship("Organization")
