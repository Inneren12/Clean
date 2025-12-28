import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.infra.db import Base
from app.domain.clients.db_models import ClientUser


class Team(Base):
    __tablename__ = "teams"

    team_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
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


class Booking(Base):
    __tablename__ = "bookings"

    booking_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    client_id: Mapped[str | None] = mapped_column(
        ForeignKey("client_users.client_id"), nullable=True, index=True
    )
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.team_id"), nullable=False)
    lead_id: Mapped[str | None] = mapped_column(ForeignKey("leads.lead_id"), nullable=True)
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
    booking_id: Mapped[str] = mapped_column(ForeignKey("bookings.booking_id"), nullable=False)
    email_type: Mapped[str] = mapped_column(String(64), nullable=False)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(String(2000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    booking: Mapped[Booking] = relationship("Booking", backref="email_events")

    __table_args__ = (
        Index("ix_email_events_booking_type", "booking_id", "email_type"),
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

    order: Mapped[Booking] = relationship("Booking", back_populates="photos")
