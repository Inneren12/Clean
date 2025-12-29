from __future__ import annotations

import io
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.bookings.db_models import Booking
from app.domain.clients.db_models import ClientUser
from app.domain.documents.db_models import Document, DocumentTemplate
from app.domain.invoices.db_models import Invoice, Payment
from app.domain.leads.db_models import Lead

DOCUMENT_TYPE_INVOICE = "invoice"
DOCUMENT_TYPE_RECEIPT = "receipt"
DOCUMENT_TYPE_SERVICE_AGREEMENT = "service_agreement"

DEFAULT_TEMPLATES: dict[str, dict[str, Any]] = {
    DOCUMENT_TYPE_INVOICE: {
        "name": "Invoice — Standard",
        "version": 1,
        "content": {
            "title": "Invoice",
            "line_item_label": "Line Items",
            "footer": "Thank you for your business.",
        },
    },
    DOCUMENT_TYPE_RECEIPT: {
        "name": "Receipt — Standard",
        "version": 1,
        "content": {
            "title": "Payment Receipt",
            "footer": "This receipt confirms payment was received.",
        },
    },
    DOCUMENT_TYPE_SERVICE_AGREEMENT: {
        "name": "Service Agreement — Standard",
        "version": 1,
        "content": {
            "title": "Service Agreement",
            "clauses": [
                "The service provider will perform cleaning services at the scheduled time.",
                "Client is responsible for providing safe access to the property.",
                "Cancellations require 24 hours' notice to avoid fees.",
                "Payment terms follow the issued invoice.",
            ],
        },
    },
}


def _escape_pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _build_pdf(lines: list[str]) -> bytes:
    content_lines = ["BT", "/F1 12 Tf", "72 750 Td", "14 TL"]
    for line in lines:
        content_lines.append(f"({_escape_pdf_text(line)}) Tj")
        content_lines.append("T*")
    content_lines.append("ET")
    stream_bytes = "\n".join(content_lines).encode("latin-1", "replace")

    buffer = io.BytesIO()
    buffer.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []

    def _write_obj(payload: bytes) -> None:
        offsets.append(buffer.tell())
        obj_number = len(offsets)
        buffer.write(f"{obj_number} 0 obj\n".encode("ascii"))
        buffer.write(payload)
        buffer.write(b"\nendobj\n")

    _write_obj(b"<< /Type /Catalog /Pages 2 0 R >>")
    _write_obj(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    page_dict = (
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
    )
    _write_obj(page_dict)
    content_header = f"<< /Length {len(stream_bytes)} >>\nstream\n".encode("ascii")
    _write_obj(content_header + stream_bytes + b"\nendstream")
    _write_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    xref_offset = buffer.tell()
    buffer.write(f"xref\n0 {len(offsets) + 1}\n".encode("ascii"))
    buffer.write(b"0000000000 65535 f \n")
    for off in offsets:
        buffer.write(f"{off:010} 00000 n \n".encode("ascii"))
    buffer.write(b"trailer\n")
    buffer.write(f"<< /Size {len(offsets) + 1} /Root 1 0 R >>\n".encode("ascii"))
    buffer.write(b"startxref\n")
    buffer.write(f"{xref_offset}\n".encode("ascii"))
    buffer.write(b"%%EOF")
    return buffer.getvalue()


def _serialize_date(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return value.isoformat()


def _format_currency(cents: int, currency: str) -> str:
    return f"{currency} {cents / 100:,.2f}"


def _invoice_snapshot(invoice: Invoice, lead: Lead | None) -> dict[str, Any]:
    return {
        "invoice_id": invoice.invoice_id,
        "invoice_number": invoice.invoice_number,
        "order_id": invoice.order_id,
        "customer": {
            "name": getattr(lead, "name", None),
            "email": getattr(lead, "email", None),
            "address": getattr(lead, "address", None),
        },
        "status": invoice.status,
        "issue_date": _serialize_date(invoice.issue_date),
        "due_date": _serialize_date(invoice.due_date),
        "currency": invoice.currency,
        "subtotal_cents": invoice.subtotal_cents,
        "tax_cents": invoice.tax_cents,
        "total_cents": invoice.total_cents,
        "notes": invoice.notes,
        "items": [
            {
                "item_id": item.item_id,
                "description": item.description,
                "qty": item.qty,
                "unit_price_cents": item.unit_price_cents,
                "line_total_cents": item.line_total_cents,
            }
            for item in invoice.items
        ],
        "payments": [
            {
                "payment_id": payment.payment_id,
                "provider": payment.provider,
                "method": payment.method,
                "amount_cents": payment.amount_cents,
                "currency": payment.currency,
                "status": payment.status,
                "received_at": _serialize_date(payment.received_at),
            }
            for payment in invoice.payments
        ],
    }


def _receipt_snapshot(invoice: Invoice, payment: Payment, lead: Lead | None) -> dict[str, Any]:
    return {
        "payment": {
            "payment_id": payment.payment_id,
            "method": payment.method,
            "amount_cents": payment.amount_cents,
            "currency": payment.currency,
            "status": payment.status,
            "received_at": _serialize_date(payment.received_at),
            "reference": payment.reference,
        },
        "invoice": {
            "invoice_id": invoice.invoice_id,
            "invoice_number": invoice.invoice_number,
            "total_cents": invoice.total_cents,
            "issue_date": _serialize_date(invoice.issue_date),
            "status": invoice.status,
        },
        "customer": {
            "name": getattr(lead, "name", None),
            "email": getattr(lead, "email", None),
            "address": getattr(lead, "address", None),
        },
    }


def _service_agreement_snapshot(
    booking: Booking, lead: Lead | None, client: ClientUser | None
) -> dict[str, Any]:
    return {
        "booking": {
            "booking_id": booking.booking_id,
            "team_id": booking.team_id,
            "starts_at": _serialize_date(booking.starts_at),
            "duration_minutes": booking.duration_minutes,
            "status": booking.status,
            "deposit_required": booking.deposit_required,
            "deposit_cents": booking.deposit_cents,
        },
        "client": {
            "name": getattr(client, "name", None),
            "email": getattr(client, "email", None),
        },
        "customer": {
            "name": getattr(lead, "name", None),
            "email": getattr(lead, "email", None),
            "address": getattr(lead, "address", None),
        },
    }


def _render_invoice_lines(template: DocumentTemplate, snapshot: dict[str, Any]) -> list[str]:
    invoice = snapshot
    customer = invoice.get("customer", {})
    lines = [template.content.get("title", "Invoice"), f"Template v{template.version}"]
    lines.append(f"Invoice #: {invoice['invoice_number']}")
    lines.append(f"Status: {invoice['status']}")
    lines.append(f"Issue Date: {invoice['issue_date']}")
    if invoice.get("due_date"):
        lines.append(f"Due Date: {invoice['due_date']}")
    lines.append(" ")
    lines.append("Bill To:")
    for field in ("name", "email", "address"):
        value = customer.get(field)
        if value:
            lines.append(str(value))
    lines.append(" ")
    lines.append(template.content.get("line_item_label", "Items"))
    for item in invoice.get("items", []):
        lines.append(
            f"- {item['qty']} x {item['description']}: "
            f"{_format_currency(item['line_total_cents'], invoice['currency'])}"
        )
    lines.append(" ")
    lines.append(f"Subtotal: {_format_currency(invoice['subtotal_cents'], invoice['currency'])}")
    lines.append(f"Tax: {_format_currency(invoice['tax_cents'], invoice['currency'])}")
    lines.append(f"Total: {_format_currency(invoice['total_cents'], invoice['currency'])}")
    paid = sum(payment.get("amount_cents", 0) for payment in invoice.get("payments", []))
    lines.append(f"Paid: {_format_currency(paid, invoice['currency'])}")
    balance = max(invoice["total_cents"] - paid, 0)
    lines.append(f"Balance Due: {_format_currency(balance, invoice['currency'])}")
    if invoice.get("notes"):
        lines.append(" ")
        lines.append("Notes:")
        lines.append(str(invoice["notes"]))
    if template.content.get("footer"):
        lines.append(" ")
        lines.append(template.content["footer"])
    return lines


def _render_receipt_lines(template: DocumentTemplate, snapshot: dict[str, Any]) -> list[str]:
    payment = snapshot["payment"]
    invoice = snapshot["invoice"]
    customer = snapshot.get("customer", {})
    lines = [template.content.get("title", "Receipt"), f"Template v{template.version}"]
    lines.append(f"Payment ID: {payment['payment_id']}")
    lines.append(f"Method: {payment['method']}")
    lines.append(f"Amount: {_format_currency(payment['amount_cents'], payment['currency'])}")
    lines.append(f"Received: {payment.get('received_at') or 'n/a'}")
    if payment.get("reference"):
        lines.append(f"Reference: {payment['reference']}")
    lines.append(" ")
    lines.append("Invoice")
    lines.append(f"Invoice #: {invoice['invoice_number']}")
    lines.append(f"Issue Date: {invoice['issue_date']}")
    lines.append(f"Invoice Total: {_format_currency(invoice['total_cents'], payment['currency'])}")
    lines.append(f"Invoice Status: {invoice['status']}")
    lines.append(" ")
    lines.append("Billed To")
    for field in ("name", "email", "address"):
        value = customer.get(field)
        if value:
            lines.append(str(value))
    if template.content.get("footer"):
        lines.append(" ")
        lines.append(template.content["footer"])
    return lines


def _render_service_agreement_lines(
    template: DocumentTemplate, snapshot: dict[str, Any]
) -> list[str]:
    booking = snapshot["booking"]
    customer = snapshot.get("customer", {})
    client = snapshot.get("client", {})
    lines = [template.content.get("title", "Service Agreement"), f"Template v{template.version}"]
    lines.append(f"Booking ID: {booking['booking_id']}")
    lines.append(f"Team: {booking['team_id']}")
    lines.append(f"Start: {booking.get('starts_at')}")
    lines.append(f"Duration (minutes): {booking['duration_minutes']}")
    lines.append(f"Status: {booking['status']}")
    lines.append(f"Deposit Required: {'Yes' if booking.get('deposit_required') else 'No'}")
    if booking.get("deposit_cents"):
        lines.append(f"Deposit Amount: {_format_currency(booking['deposit_cents'], 'CAD')}")
    lines.append(" ")
    lines.append("Client")
    for field in ("name", "email"):
        value = client.get(field)
        if value:
            lines.append(str(value))
    lines.append(" ")
    lines.append("Service Location")
    for field in ("name", "email", "address"):
        value = customer.get(field)
        if value:
            lines.append(str(value))
    clauses = template.content.get("clauses", [])
    if clauses:
        lines.append(" ")
        lines.append("Terms")
        for clause in clauses:
            lines.append(f"- {clause}")
    return lines


def _render_pdf(document_type: str, template: DocumentTemplate, snapshot: dict[str, Any]) -> bytes:
    if document_type == DOCUMENT_TYPE_INVOICE:
        lines = _render_invoice_lines(template, snapshot)
    elif document_type == DOCUMENT_TYPE_RECEIPT:
        lines = _render_receipt_lines(template, snapshot)
    elif document_type == DOCUMENT_TYPE_SERVICE_AGREEMENT:
        lines = _render_service_agreement_lines(template, snapshot)
    else:
        raise ValueError(f"Unknown document type: {document_type}")
    return _build_pdf(lines)


async def ensure_default_templates(session: AsyncSession) -> None:
    for document_type, spec in DEFAULT_TEMPLATES.items():
        existing = await session.scalar(
            select(DocumentTemplate).where(
                DocumentTemplate.document_type == document_type,
                DocumentTemplate.version == spec["version"],
            )
        )
        if existing:
            continue
        try:
            async with session.begin_nested():
                template = DocumentTemplate(
                    document_type=document_type,
                    name=spec["name"],
                    version=spec["version"],
                    content=spec["content"],
                    is_active=True,
                )
                session.add(template)
                await session.flush()
        except IntegrityError:
            await session.rollback()
            continue


async def _latest_template(session: AsyncSession, document_type: str) -> DocumentTemplate:
    stmt = (
        select(DocumentTemplate)
        .where(DocumentTemplate.document_type == document_type, DocumentTemplate.is_active.is_(True))
        .order_by(DocumentTemplate.version.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    template = result.scalar_one_or_none()
    if template is None:
        raise ValueError(f"No template available for {document_type}")
    return template


async def _get_existing_document(
    session: AsyncSession, document_type: str, reference_id: str
) -> Document | None:
    stmt = (
        select(Document)
        .options(selectinload(Document.template))
        .where(Document.document_type == document_type, Document.reference_id == reference_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def _document_pdf_bytes(document: Document) -> bytes:
    return bytes(document.pdf_bytes)


async def _create_document(
    session: AsyncSession,
    *,
    document_type: str,
    reference_id: str,
    snapshot: dict[str, Any],
    template: DocumentTemplate,
) -> Document:
    pdf_bytes = _render_pdf(document_type, template, snapshot)
    document = Document(
        document_type=document_type,
        reference_id=reference_id,
        template_id=template.template_id,
        template_version=template.version,
        snapshot=snapshot,
        pdf_bytes=pdf_bytes,
    )
    session.add(document)
    return document


async def get_or_create_invoice_document(
    session: AsyncSession, *, invoice: Invoice, lead: Lead | None
) -> Document:
    await ensure_default_templates(session)
    existing = await _get_existing_document(session, DOCUMENT_TYPE_INVOICE, invoice.invoice_id)
    if existing:
        return existing
    template = await _latest_template(session, DOCUMENT_TYPE_INVOICE)
    snapshot = _invoice_snapshot(invoice, lead)
    document = await _create_document(
        session,
        document_type=DOCUMENT_TYPE_INVOICE,
        reference_id=invoice.invoice_id,
        snapshot=snapshot,
        template=template,
    )
    await session.flush()
    return document


async def get_or_create_receipt_document(
    session: AsyncSession, *, invoice: Invoice, payment: Payment, lead: Lead | None
) -> Document:
    await ensure_default_templates(session)
    existing = await _get_existing_document(session, DOCUMENT_TYPE_RECEIPT, payment.payment_id)
    if existing:
        return existing
    template = await _latest_template(session, DOCUMENT_TYPE_RECEIPT)
    snapshot = _receipt_snapshot(invoice, payment, lead)
    document = await _create_document(
        session,
        document_type=DOCUMENT_TYPE_RECEIPT,
        reference_id=payment.payment_id,
        snapshot=snapshot,
        template=template,
    )
    await session.flush()
    return document


async def get_or_create_service_agreement_document(
    session: AsyncSession, *, booking: Booking, lead: Lead | None, client: ClientUser | None
) -> Document:
    await ensure_default_templates(session)
    existing = await _get_existing_document(session, DOCUMENT_TYPE_SERVICE_AGREEMENT, booking.booking_id)
    if existing:
        return existing
    template = await _latest_template(session, DOCUMENT_TYPE_SERVICE_AGREEMENT)
    snapshot = _service_agreement_snapshot(booking, lead, client)
    document = await _create_document(
        session,
        document_type=DOCUMENT_TYPE_SERVICE_AGREEMENT,
        reference_id=booking.booking_id,
        snapshot=snapshot,
        template=template,
    )
    await session.flush()
    return document


def render_document_from_snapshot(document: Document, template: DocumentTemplate) -> bytes:
    return _render_pdf(document.document_type, template, document.snapshot)


def pdf_bytes(document: Document) -> bytes:
    return _document_pdf_bytes(document)
