from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.infra.db import UUID_TYPE
from app.infra.db import Base
from app.settings import settings


class Dispute(Base):
    __tablename__ = "disputes"
    __table_args__ = (
        Index("ix_disputes_org_id", "org_id"),
        Index("ix_disputes_org_state", "org_id", "state"),
        Index("ix_disputes_booking_state", "booking_id", "state"),
    )

    dispute_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    booking_id: Mapped[str] = mapped_column(
        ForeignKey("bookings.booking_id"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("organizations.org_id", ondelete="CASCADE"),
        nullable=False,
        default=lambda: settings.default_org_id,
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))
    opened_by: Mapped[str | None] = mapped_column(String(100))
    facts_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    decision: Mapped[str | None] = mapped_column(String(32))
    decision_cents: Mapped[int | None] = mapped_column(Integer)
    decision_notes: Mapped[str | None] = mapped_column(String(500))
    decision_snapshot: Mapped[dict | None] = mapped_column(JSON)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    booking = relationship("Booking", backref="disputes")


class FinancialAdjustmentEvent(Base):
    __tablename__ = "financial_adjustment_events"
    __table_args__ = (
        Index("ix_financial_events_org_id", "org_id"),
        Index("ix_financial_events_org_created", "org_id", "created_at"),
        Index("ix_financial_events_booking", "booking_id", "created_at"),
    )

    event_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    booking_id: Mapped[str] = mapped_column(
        ForeignKey("bookings.booking_id"), nullable=False, index=True
    )
    dispute_id: Mapped[str] = mapped_column(
        ForeignKey("disputes.dispute_id"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("organizations.org_id", ondelete="CASCADE"),
        nullable=False,
        default=lambda: settings.default_org_id,
    )
    adjustment_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    before_totals: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    after_totals: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    dispute = relationship("Dispute", backref="events")
