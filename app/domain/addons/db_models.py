from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infra.db import Base


class AddonDefinition(Base):
    __tablename__ = "addon_definitions"

    addon_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    default_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    order_addons: Mapped[list["OrderAddon"]] = relationship("OrderAddon", back_populates="definition")


class OrderAddon(Base):
    __tablename__ = "order_addons"

    order_addon_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("bookings.booking_id", ondelete="CASCADE"), nullable=False, index=True
    )
    addon_id: Mapped[int] = mapped_column(
        ForeignKey("addon_definitions.addon_id"), nullable=False, index=True
    )
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_price_cents_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)
    minutes_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    definition: Mapped[AddonDefinition] = relationship("AddonDefinition", back_populates="order_addons")
    order = relationship("Booking", back_populates="order_addons")

    __table_args__ = (
        UniqueConstraint("order_id", "addon_id", name="uq_order_addons_order_addon"),
    )
