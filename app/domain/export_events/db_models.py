from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


class ExportEvent(Base):
    __tablename__ = "export_events"
    __table_args__ = (Index("ix_export_events_created_lead", "created_at", "lead_id"),)

    event_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
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
