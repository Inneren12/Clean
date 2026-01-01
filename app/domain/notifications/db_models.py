from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.saas.db_models import UUID_TYPE
from app.infra.db import Base
from app.settings import settings


class EmailFailure(Base):
    __tablename__ = "email_failures"

    failure_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("organizations.org_id", ondelete="CASCADE"),
        nullable=False,
        default=lambda: settings.default_org_id,
    )
    email_event_id: Mapped[str | None] = mapped_column(
        ForeignKey("email_events.event_id", ondelete="SET NULL"), nullable=True, index=True
    )
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    email_type: Mapped[str] = mapped_column(String(64), nullable=False)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(String(2000), nullable=False)
    booking_id: Mapped[str | None] = mapped_column(ForeignKey("bookings.booking_id"), nullable=True)
    invoice_id: Mapped[str | None] = mapped_column(ForeignKey("invoices.invoice_id"), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    last_error: Mapped[str | None] = mapped_column(String(255))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_email_failures_org_status", "org_id", "status", "next_retry_at"),
        Index("ix_email_failures_org_dedupe", "org_id", "dedupe_key"),
    )


class Unsubscribe(Base):
    __tablename__ = "unsubscribe"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("organizations.org_id", ondelete="CASCADE"),
        nullable=False,
        default=lambda: settings.default_org_id,
    )
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_unsubscribe_org_recipient", "org_id", "recipient"),
        sa.UniqueConstraint("org_id", "recipient", "scope", name="uq_unsubscribe_recipient_scope"),
    )
