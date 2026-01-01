from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.infra.db import Base, UUID_TYPE
from app.domain.clients.db_models import ClientUser
from app.infra.db import UUID_TYPE
from app.settings import settings

if TYPE_CHECKING:  # pragma: no cover
    from app.domain.workers.db_models import Worker
    from app.domain.invoices.db_models import Invoice


class Team(Base):
    __tablename__ = "teams"

    team_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("organizations.org_id", ondelete="CASCADE"),
        nullable=False,
        default=lambda: settings.default_org_id,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    bookings: Mapped[list["Booking"]] = relationship("Booking", back_populates="team")

    __table_args__ = (Index("ix_teams_org_id", "org_id"),)


class Booking(Base):
    __tablename__ = "bookings"

    booking_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        sa.ForeignKey("organizations.org_id", ondelete="CASCADE"),
        nullable=False,
        default=lambda: settings.default_org_id,
    )
    client_id: Mapped[str | None] = mapped_column(
        ForeignKey("client_users.client_id"), nullable=True, index=True
    )
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.team_id"), nullable=False)
    lead_id: Mapped[str | None] = mapped_column(ForeignKey("leads.lead_id"), nullable=True)
    assigned_worker_id: Mapped[int | None] = mapped_column(
        ForeignKey("workers.worker_id"), nullable=True, index=True
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_duration_minutes: Mapped[int | None] = mapped_column(Integer)
    planned_minutes: Mapped[int | None] = mapped_column(Integer)
    actual_seconds: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    subscription_id: Mapped[str | None] = mapped_column(
        ForeignKey("subscriptions.subscription_id"), index=True
    )
    scheduled_date: Mapped[date | None] = mapped_column(Date)
    deposit_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deposit_cents: Mapped[int | None] = mapped_column(Integer)
    deposit_policy: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    deposit_status: Mapped[str | None] = mapped_column(String(32))
    base_charge_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    refund_total_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    credit_note_total_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    risk_band: Mapped[str] = mapped_column(
        String(16), nullable=False, default="LOW", server_default="LOW"
    )
    risk_reasons: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False, server_default="[]"
    )
    policy_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cancellation_exception: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    cancellation_exception_note: Mapped[str | None] = mapped_column(String(255))
    stripe_checkout_session_id: Mapped[str | None] = mapped_column(String(255))
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(255))
    consent_photos: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    team: Mapped[Team] = relationship("Team", back_populates="bookings")
    client: Mapped[ClientUser | None] = relationship("ClientUser")
    assigned_worker: Mapped["Worker | None"] = relationship(
        "Worker", back_populates="bookings"
    )
    lead = relationship("Lead", backref="bookings")
    subscription = relationship("Subscription", back_populates="orders")
    photos: Mapped[list["OrderPhoto"]] = relationship(
        "OrderPhoto",
        back_populates="order",
        cascade="all, delete-orphan",
    )
    order_addons: Mapped[list["OrderAddon"]] = relationship(
        "OrderAddon",
        back_populates="order",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_bookings_org_id", "org_id"),
        Index("ix_bookings_org_status", "org_id", "status"),
        Index("ix_bookings_org_created_at", "org_id", "created_at"),
        Index("ix_bookings_org_starts_at", "org_id", "starts_at"),
        Index("ix_bookings_starts_status", "starts_at", "status"),
        Index("ix_bookings_status", "status"),
        Index("ix_bookings_checkout_session", "stripe_checkout_session_id"),
        UniqueConstraint("subscription_id", "scheduled_date", name="uq_bookings_subscription_schedule"),
    )


class EmailEvent(Base):
    __tablename__ = "email_events"

    event_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    booking_id: Mapped[str | None] = mapped_column(
        ForeignKey("bookings.booking_id"), nullable=True, index=True
    )
    invoice_id: Mapped[str | None] = mapped_column(
        ForeignKey("invoices.invoice_id"), nullable=True, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("organizations.org_id", ondelete="CASCADE"),
        nullable=False,
        default=lambda: settings.default_org_id,
    )
    email_type: Mapped[str] = mapped_column(String(64), nullable=False)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(String(2000), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    booking: Mapped[Booking | None] = relationship("Booking", backref="email_events")
    invoice: Mapped[Optional["Invoice"]] = relationship("Invoice", backref="email_events")

    __table_args__ = (
        Index("ix_email_events_org_id", "org_id"),
        Index("ix_email_events_org_created_at", "org_id", "created_at"),
        Index("ix_email_events_booking_type", "booking_id", "email_type"),
        Index("ix_email_events_invoice_type", "invoice_id", "email_type"),
        UniqueConstraint("org_id", "dedupe_key", name="uq_email_events_org_dedupe"),
    )


class OrderPhoto(Base):
    __tablename__ = "order_photos"

    photo_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    order_id: Mapped[str] = mapped_column(
        ForeignKey("bookings.booking_id", ondelete="CASCADE"), nullable=False, index=True
    )
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("organizations.org_id", ondelete="CASCADE"),
        nullable=False,
        default=lambda: settings.default_org_id,
    )

    order: Mapped[Booking] = relationship("Booking", back_populates="photos")

    __table_args__ = (
        Index("ix_order_photos_org_id", "org_id"),
        Index("ix_order_photos_org_order", "org_id", "order_id"),
    )


class OrderPhotoTombstone(Base):
    __tablename__ = "order_photo_tombstones"

    tombstone_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("organizations.org_id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("bookings.booking_id", ondelete="CASCADE"), nullable=False
    )
    photo_id: Mapped[str] = mapped_column(String(36), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(255))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_order_photo_tombstones_pending", "processed_at", "created_at"),
        Index("ix_order_photo_tombstones_org", "org_id"),
    )


class TeamWorkingHours(Base):
    __tablename__ = "team_working_hours"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.team_id"), nullable=False)
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[time] = mapped_column(Time(timezone=False), nullable=False)
    end_time: Mapped[time] = mapped_column(Time(timezone=False), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    team: Mapped[Team] = relationship("Team", backref="working_hours")

    __table_args__ = (UniqueConstraint("team_id", "day_of_week", name="uq_team_day"),)


class TeamBlackout(Base):
    __tablename__ = "team_blackouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.team_id"), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    team: Mapped[Team] = relationship("Team", backref="blackouts")
