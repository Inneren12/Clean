from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base, UUID_TYPE
from app.settings import settings


class DataDeletionRequest(Base):
    __tablename__ = "data_deletion_requests"

    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("organizations.org_id", ondelete="CASCADE"),
        nullable=False,
        default=lambda: settings.default_org_id,
    )
    lead_id: Mapped[str | None] = mapped_column(String(36))
    email: Mapped[str | None] = mapped_column(String(255))
    reason: Mapped[str | None] = mapped_column(String(255))
    requested_by: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_notes: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        Index("ix_data_deletion_requests_org_status", "org_id", "status"),
        Index("ix_data_deletion_requests_org_created", "org_id", "requested_at"),
    )
