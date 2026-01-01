from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.infra.db import UUID_TYPE
from app.infra.db import Base
from app.settings import settings


class DocumentTemplate(Base):
    __tablename__ = "document_templates"

    template_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    documents: Mapped[list["Document"]] = relationship("Document", back_populates="template")

    __table_args__ = (
        UniqueConstraint("document_type", "version", name="uq_document_template_version"),
    )


class Document(Base):
    __tablename__ = "documents"

    document_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("organizations.org_id", ondelete="CASCADE"),
        nullable=False,
        default=lambda: settings.default_org_id,
    )
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    reference_id: Mapped[str] = mapped_column(String(64), nullable=False)
    template_id: Mapped[int] = mapped_column(ForeignKey("document_templates.template_id"), nullable=False)
    template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    pdf_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    template: Mapped[DocumentTemplate] = relationship("DocumentTemplate", back_populates="documents")

    __table_args__ = (
        Index("ix_documents_org_id", "org_id"),
        Index("ix_documents_org_type", "org_id", "document_type"),
        UniqueConstraint("document_type", "reference_id", name="uq_document_reference"),
        Index("ix_documents_reference_type", "reference_id", "document_type"),
    )
