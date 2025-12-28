from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infra.db import Base


class ReasonLog(Base):
    __tablename__ = "reason_logs"

    reason_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    order_id: Mapped[str] = mapped_column(
        ForeignKey("bookings.booking_id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(100))
    time_entry_id: Mapped[str | None] = mapped_column(
        ForeignKey("work_time_entries.entry_id"), nullable=True
    )
    invoice_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoice_items.item_id"), nullable=True
    )

    order = relationship("Booking")
    time_entry = relationship("WorkTimeEntry")
    invoice_item = relationship("InvoiceItem")

    __table_args__ = (
        Index("ix_reason_logs_order", "order_id"),
        Index("ix_reason_logs_kind", "kind"),
        Index("ix_reason_logs_created_at", "created_at"),
    )
