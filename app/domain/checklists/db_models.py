import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infra.db import Base


class ChecklistTemplate(Base):
    __tablename__ = "checklist_templates"

    template_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    service_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    items: Mapped[list["ChecklistTemplateItem"]] = relationship(
        "ChecklistTemplateItem",
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="ChecklistTemplateItem.position",
    )

    __table_args__ = (
        UniqueConstraint("service_type", "version", name="uq_checklist_template_version"),
    )


class ChecklistTemplateItem(Base):
    __tablename__ = "checklist_template_items"

    item_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("checklist_templates.template_id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    template: Mapped[ChecklistTemplate] = relationship("ChecklistTemplate", back_populates="items")


class ChecklistRun(Base):
    __tablename__ = "checklist_runs"

    run_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    order_id: Mapped[str] = mapped_column(ForeignKey("bookings.booking_id"), nullable=False)
    template_id: Mapped[int] = mapped_column(ForeignKey("checklist_templates.template_id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="in_progress")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    template: Mapped[ChecklistTemplate] = relationship("ChecklistTemplate")
    items: Mapped[list["ChecklistRunItem"]] = relationship(
        "ChecklistRunItem", back_populates="run", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("order_id", name="uq_checklist_run_order"),)


class ChecklistRunItem(Base):
    __tablename__ = "checklist_run_items"

    run_item_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("checklist_runs.run_id"), nullable=False)
    template_item_id: Mapped[int] = mapped_column(
        ForeignKey("checklist_template_items.item_id"), nullable=False
    )
    checked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    run: Mapped[ChecklistRun] = relationship("ChecklistRun", back_populates="items")
    template_item: Mapped[ChecklistTemplateItem] = relationship("ChecklistTemplateItem")
