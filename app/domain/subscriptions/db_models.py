from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infra.db import Base

DEFAULT_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class Subscription(Base):
    __tablename__ = "subscriptions"

    subscription_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.org_id"), nullable=False, default=DEFAULT_ORG_ID
    )
    client_id: Mapped[str] = mapped_column(ForeignKey("client_users.client_id"), index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    frequency: Mapped[str] = mapped_column(String(16), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    preferred_weekday: Mapped[int | None] = mapped_column(Integer)
    preferred_day_of_month: Mapped[int | None] = mapped_column(Integer)
    base_service_type: Mapped[str] = mapped_column(String(100), nullable=False)
    base_price: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    client = relationship("ClientUser", backref="subscriptions")
    addons: Mapped[list["SubscriptionAddon"]] = relationship(
        "SubscriptionAddon", back_populates="subscription", cascade="all, delete-orphan"
    )
    orders = relationship("Booking", back_populates="subscription")

    __table_args__ = (
        Index("ix_subscriptions_org_status", "org_id", "status"),
        Index("ix_subscriptions_org_next_run", "org_id", "next_run_at"),
    )


class SubscriptionAddon(Base):
    __tablename__ = "subscription_addons"

    subscription_addon_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.org_id"), nullable=False, default=DEFAULT_ORG_ID
    )
    subscription_id: Mapped[str] = mapped_column(
        ForeignKey("subscriptions.subscription_id", ondelete="CASCADE"), index=True
    )
    addon_code: Mapped[str] = mapped_column(String(100), nullable=False)

    subscription: Mapped[Subscription] = relationship("Subscription", back_populates="addons")

    __table_args__ = (
        UniqueConstraint("subscription_id", "addon_code", name="uq_subscription_addons_code"),
        Index("ix_subscription_addons_org_subscription", "org_id", "subscription_id"),
    )
