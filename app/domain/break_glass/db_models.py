import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base, UUID_TYPE


class BreakGlassSession(Base):
    __tablename__ = "break_glass_sessions"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, sa.ForeignKey("organizations.org_id", ondelete="CASCADE"), nullable=False
    )
    actor: Mapped[str] = mapped_column(sa.String(150), nullable=False)
    reason: Mapped[str] = mapped_column(sa.Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    __table_args__ = (
        sa.Index("ix_break_glass_sessions_org", "org_id"),
        sa.Index("ix_break_glass_sessions_org_expires", "org_id", "expires_at"),
    )
