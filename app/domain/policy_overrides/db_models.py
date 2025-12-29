import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func, event
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.infra.db import Base

class PolicyOverrideAudit(Base):
    __tablename__ = "policy_override_audits"

    audit_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    booking_id: Mapped[str] = mapped_column(
        ForeignKey("bookings.booking_id"), nullable=False, index=True
    )
    override_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    old_value: Mapped[dict] = mapped_column(JSON, nullable=False)
    new_value: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_policy_override_booking_type", "booking_id", "override_type"),
    )


@event.listens_for(PolicyOverrideAudit, "before_update", propagate=True)
def _prevent_audit_updates(mapper, connection, target) -> None:  # noqa: ARG001
    raise ValueError("Policy override audit records are immutable")


@event.listens_for(PolicyOverrideAudit, "before_delete", propagate=True)
def _prevent_audit_deletes(mapper, connection, target) -> None:  # noqa: ARG001
    raise ValueError("Policy override audit records cannot be deleted")
