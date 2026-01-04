from __future__ import annotations

import secrets
import string
import uuid
from datetime import datetime

from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.domain.leads.statuses import default_lead_status
from app.infra.db import UUID_TYPE
from app.infra.db import Base
from app.settings import settings


CHARSET = string.ascii_uppercase + string.digits
CODE_LENGTH = 8


def generate_referral_code() -> str:
    return "".join(secrets.choice(CHARSET) for _ in range(CODE_LENGTH))


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
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("organizations.org_id", ondelete="CASCADE"),
        nullable=False,
        default=lambda: settings.default_org_id,
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
    pending_deletion: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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

    __table_args__ = (
        Index("ix_leads_org_id", "org_id"),
        Index("ix_leads_org_status", "org_id", "status"),
        Index("ix_leads_org_created_at", "org_id", "created_at"),
    )

    def __init__(self, **kwargs: object) -> None:
        if not kwargs.get("referral_code"):
            kwargs["referral_code"] = generate_referral_code()
        super().__init__(**kwargs)


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
