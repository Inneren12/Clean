from __future__ import annotations

import uuid
from datetime import datetime

from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.infra.db import Base
from app.domain.leads.statuses import default_lead_status


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    brand: Mapped[str] = mapped_column(String(32), nullable=False, default="economy")
    state_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Lead(Base):
    __tablename__ = "leads"

    lead_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    postal_code: Mapped[str | None] = mapped_column(String(32))
    address: Mapped[str | None] = mapped_column(String(255))
    preferred_dates: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    access_notes: Mapped[str | None] = mapped_column(String(255))
    parking: Mapped[str | None] = mapped_column(String(255))
    pets: Mapped[str | None] = mapped_column(String(255))
    allergies: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(String(500))
    structured_inputs: Mapped[dict] = mapped_column(JSON, nullable=False)
    estimate_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    pricing_config_version: Mapped[str] = mapped_column(String(32), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=default_lead_status)
    utm_source: Mapped[str | None] = mapped_column(String(100))
    utm_medium: Mapped[str | None] = mapped_column(String(100))
    utm_campaign: Mapped[str | None] = mapped_column(String(100))
    utm_term: Mapped[str | None] = mapped_column(String(100))
    utm_content: Mapped[str | None] = mapped_column(String(100))
    referrer: Mapped[str | None] = mapped_column(String(255))
    referral_code: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        unique=True,
        default=lambda: str(uuid.uuid4()).replace("-", "")[:16],
    )
    referred_by_code: Mapped[str | None] = mapped_column(String(16))
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

    referral_credits: Mapped[list["ReferralCredit"]] = relationship(
        "ReferralCredit",
        back_populates="referrer",
        foreign_keys="ReferralCredit.referrer_lead_id",
    )
    referred_credit: Mapped[Optional["ReferralCredit"]] = relationship(
        "ReferralCredit",
        back_populates="referred",
        foreign_keys="ReferralCredit.referred_lead_id",
        uselist=False,
    )


class ReferralCredit(Base):
    __tablename__ = "referral_credits"

    credit_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    referrer_lead_id: Mapped[str] = mapped_column(
        ForeignKey("leads.lead_id"), nullable=False
    )
    referred_lead_id: Mapped[str] = mapped_column(
        ForeignKey("leads.lead_id"), nullable=False, unique=True
    )
    applied_code: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    referrer: Mapped[Lead] = relationship(
        Lead, back_populates="referral_credits", foreign_keys=[referrer_lead_id]
    )
    referred: Mapped[Lead] = relationship(
        Lead, back_populates="referred_credit", foreign_keys=[referred_lead_id]
    )
