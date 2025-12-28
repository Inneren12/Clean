import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infra.db import Base


class NpsResponse(Base):
    __tablename__ = "nps_responses"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    order_id: Mapped[str] = mapped_column(
        ForeignKey("bookings.booking_id", ondelete="CASCADE"), nullable=False, unique=True
    )
    client_id: Mapped[str | None] = mapped_column(
        ForeignKey("client_users.client_id"), nullable=True
    )
    score: Mapped[int] = mapped_column(nullable=False)
    comment: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    order = relationship("Booking")
    client = relationship("ClientUser")

    __table_args__ = (UniqueConstraint("order_id", name="uq_nps_responses_order"),)


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    order_id: Mapped[str] = mapped_column(
        ForeignKey("bookings.booking_id", ondelete="CASCADE"), nullable=False, unique=True
    )
    client_id: Mapped[str | None] = mapped_column(
        ForeignKey("client_users.client_id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="OPEN")
    priority: Mapped[str] = mapped_column(String(32), nullable=False, default="normal")
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(String(4000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    order = relationship("Booking")
    client = relationship("ClientUser")

    __table_args__ = (UniqueConstraint("order_id", name="uq_support_tickets_order"),)
