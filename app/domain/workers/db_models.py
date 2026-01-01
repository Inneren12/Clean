from datetime import datetime
from typing import TYPE_CHECKING

import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infra.db import Base

if TYPE_CHECKING:  # pragma: no cover
    from app.domain.bookings.db_models import Booking, Team

DEFAULT_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class Worker(Base):
    __tablename__ = "workers"

    worker_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.org_id"), nullable=False, default=DEFAULT_ORG_ID
    )
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.team_id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str] = mapped_column(String(50), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str | None] = mapped_column(String(80))
    hourly_rate_cents: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
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

    team: Mapped["Team"] = relationship("Team")
    bookings: Mapped[list["Booking"]] = relationship(
        "Booking", back_populates="assigned_worker"
    )

    __table_args__ = (
        Index("ix_workers_org_id", "org_id"),
        Index("ix_workers_org_active", "org_id", "is_active"),
    )
