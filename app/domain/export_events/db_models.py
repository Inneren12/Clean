from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base

DEFAULT_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class ExportEvent(Base):
    __tablename__ = "export_events"
    __table_args__ = (
        Index("ix_export_events_created_lead", "created_at", "lead_id"),
        Index("ix_export_events_org_created", "org_id", "created_at"),
    )

    event_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.org_id"), nullable=False, default=DEFAULT_ORG_ID
    )
    lead_id: Mapped[str | None] = mapped_column(String(36))
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    target_url_host: Mapped[str | None] = mapped_column(String(255))
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
