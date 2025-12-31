from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.infra.db import Base


class MembershipRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    DISPATCHER = "dispatcher"
    FINANCE = "finance"
    VIEWER = "viewer"
    WORKER = "worker"


class Organization(Base):
    __tablename__ = "organizations"

    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
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

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(sa.String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, server_default=sa.true())
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
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, sa.ForeignKey("organizations.org_id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, sa.ForeignKey("users.user_id", ondelete="CASCADE"))
    role: Mapped[MembershipRole] = mapped_column(sa.Enum(MembershipRole), nullable=False)
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
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, sa.ForeignKey("organizations.org_id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    role: Mapped[MembershipRole] = mapped_column(sa.Enum(MembershipRole), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    organization: Mapped[Organization] = relationship("Organization", back_populates="api_tokens")


class OrganizationBilling(Base):
    __tablename__ = "organization_billing"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, sa.ForeignKey("organizations.org_id", ondelete="CASCADE"))
    stripe_customer_id: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    plan_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="inactive", server_default="inactive")
    current_period_end: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    organization: Mapped[Organization] = relationship("Organization")


class OrganizationUsageEvent(Base):
    __tablename__ = "organization_usage_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, sa.ForeignKey("organizations.org_id", ondelete="CASCADE"))
    metric: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    quantity: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1, server_default="1")
    resource_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    organization: Mapped[Organization] = relationship("Organization")
