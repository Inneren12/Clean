from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.infra.db import Base

DEFAULT_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class DocumentTemplate(Base):
    __tablename__ = "document_templates"

    template_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.org_id"), nullable=False, default=DEFAULT_ORG_ID
    )
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    documents: Mapped[list["Document"]] = relationship("Document", back_populates="template")

    __table_args__ = (
        UniqueConstraint("document_type", "version", name="uq_document_template_version"),
        Index("ix_document_templates_org_type", "org_id", "document_type"),
    )


class Document(Base):
    __tablename__ = "documents"

    document_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.org_id"), nullable=False, default=DEFAULT_ORG_ID
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
        UniqueConstraint("document_type", "reference_id", name="uq_document_reference"),
        Index("ix_documents_reference_type", "reference_id", "document_type"),
        Index("ix_documents_org_type", "org_id", "document_type"),
    )
