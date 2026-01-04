from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infra.db import UUID_TYPE
from app.infra.db import Base
from app.settings import settings


class InvoiceNumberSequence(Base):
    __tablename__ = "invoice_number_sequences"

    sequence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    last_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (UniqueConstraint("year", name="uq_invoice_number_sequences_year"),)


class Invoice(Base):
    __tablename__ = "invoices"

    invoice_id: Mapped[str] = mapped_column(
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
    invoice_number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("bookings.booking_id"), index=True)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("leads.lead_id"), index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    subtotal_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    taxable_subtotal_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tax_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    tax_rate_basis: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(1000))
    created_by: Mapped[str | None] = mapped_column(String(100))
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

    items: Mapped[list["InvoiceItem"]] = relationship(
        "InvoiceItem",
        back_populates="invoice",
        cascade="all, delete-orphan",
    )
    payments: Mapped[list["Payment"]] = relationship(
        "Payment",
        back_populates="invoice",
        cascade="all, delete-orphan",
    )
    public_token: Mapped[InvoicePublicToken | None] = relationship(
        "InvoicePublicToken",
        back_populates="invoice",
        cascade="all, delete-orphan",
        uselist=False,
    )

    __table_args__ = (
        Index("ix_invoices_org_id", "org_id"),
        Index("ix_invoices_org_status", "org_id", "status"),
        Index("ix_invoices_org_created_at", "org_id", "created_at"),
    )


class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    item_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.invoice_id"), nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    line_total_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))

    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="items")


class Payment(Base):
    __tablename__ = "invoice_payments"

    payment_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    invoice_id: Mapped[str | None] = mapped_column(
        ForeignKey("invoices.invoice_id"), nullable=True, index=True
    )
    booking_id: Mapped[str | None] = mapped_column(
        ForeignKey("bookings.booking_id"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_ref: Mapped[str | None] = mapped_column(String(255))
    checkout_session_id: Mapped[str | None] = mapped_column(String(255))
    payment_intent_id: Mapped[str | None] = mapped_column(String(255))
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reference: Mapped[str | None] = mapped_column(String(255))
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("organizations.org_id", ondelete="CASCADE"),
        nullable=False,
        default=lambda: settings.default_org_id,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    invoice: Mapped[Invoice | None] = relationship("Invoice", back_populates="payments")

    __table_args__ = (
        Index("ix_invoice_payments_org_id", "org_id"),
        Index("ix_invoice_payments_org_status", "org_id", "status"),
        Index("ix_invoice_payments_invoice_status", "invoice_id", "status"),
        Index("ix_invoice_payments_provider_ref", "provider_ref"),
        Index("ix_invoice_payments_checkout_session", "checkout_session_id"),
        UniqueConstraint("provider", "provider_ref", name="uq_invoice_payments_provider_ref"),
    )


class StripeEvent(Base):
    __tablename__ = "stripe_events"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("organizations.org_id", ondelete="CASCADE"),
        nullable=True,
    )
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_stripe_events_payload_hash", "payload_hash"),
        Index("ix_stripe_events_org_id", "org_id"),
    )


class InvoicePublicToken(Base):
    __tablename__ = "invoice_public_tokens"

    token_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[str] = mapped_column(
        ForeignKey("invoices.invoice_id"),
        nullable=False,
        unique=True,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="public_token")
