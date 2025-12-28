from __future__ import annotations

from html import escape
import io
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.invoices import schemas as invoice_schemas, service as invoice_service, statuses as invoice_statuses
from app.infra.db import get_db_session
from app.infra import stripe as stripe_infra
from app.settings import settings

router = APIRouter(include_in_schema=False)
logger = logging.getLogger(__name__)


def _format_currency(cents: int, currency: str) -> str:
    return f"{currency} {cents / 100:,.2f}"


def _render_invoice_html(context: dict) -> str:
    invoice = context["invoice"]
    customer = context["customer"]
    token = context.get("token")
    rows = []
    rows.append("<h1>Invoice</h1>")
    rows.append(f"<p><strong>Invoice #:</strong> {escape(invoice['invoice_number'])}</p>")
    rows.append(f"<p><strong>Status:</strong> {escape(invoice['status'])}</p>")
    rows.append(f"<p><strong>Issue Date:</strong> {escape(str(invoice['issue_date']))}</p>")
    if invoice.get("due_date"):
        rows.append(f"<p><strong>Due Date:</strong> {escape(str(invoice['due_date']))}</p>")
    if token:
        rows.append(f"<p><a href=\"/i/{escape(token)}.pdf\">Download PDF</a></p>")

    rows.append("<h2>Bill To</h2>")
    if customer.get("name"):
        rows.append(f"<p>{escape(customer['name'])}</p>")
    if customer.get("email"):
        rows.append(f"<p>{escape(customer['email'])}</p>")
    if customer.get("address"):
        rows.append(f"<p>{escape(customer['address'])}</p>")

    rows.append("<h2>Items</h2>")
    rows.append(
        "<table border=\"1\" cellpadding=\"6\" cellspacing=\"0\" width=\"100%\">"
        "<tr><th align=\"left\">Description</th><th>Qty</th><th align=\"right\">Unit Price" \
        "</th><th align=\"right\">Line Total</th></tr>"
    )
    for item in invoice.get("items", []):
        rows.append(
            "<tr>"
            f"<td>{escape(item['description'])}</td>"
            f"<td align=\"center\">{item['qty']}</td>"
            f"<td align=\"right\">{_format_currency(item['unit_price_cents'], invoice['currency'])}</td>"
            f"<td align=\"right\">{_format_currency(item['line_total_cents'], invoice['currency'])}</td>"
            "</tr>"
        )
    rows.append("</table>")

    rows.append("<h2>Totals</h2>")
    rows.append(
        f"<p>Subtotal: {_format_currency(invoice['subtotal_cents'], invoice['currency'])}<br>"
        f"Tax: {_format_currency(invoice['tax_cents'], invoice['currency'])}<br>"
        f"Total: <strong>{_format_currency(invoice['total_cents'], invoice['currency'])}</strong></p>"
    )
    if invoice.get("balance_due_cents") is not None:
        rows.append(
            f"<p>Balance Due: {_format_currency(invoice['balance_due_cents'], invoice['currency'])}</p>"
        )
    if invoice.get("notes"):
        rows.append(f"<h3>Notes</h3><p>{escape(invoice['notes'])}</p>")

    return "\n".join(rows)


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


def _render_invoice_pdf(context: dict) -> bytes:
    invoice = context["invoice"]
    customer = context["customer"]
    lines = [
        f"Invoice {invoice['invoice_number']}",
        f"Status: {invoice['status']}",
        f"Issue Date: {invoice['issue_date']}",
    ]
    if invoice.get("due_date"):
        lines.append(f"Due Date: {invoice['due_date']}")
    lines.append(" ")
    lines.append("Bill To:")
    if customer.get("name"):
        lines.append(str(customer["name"]))
    if customer.get("email"):
        lines.append(str(customer["email"]))
    if customer.get("address"):
        lines.append(str(customer["address"]))
    lines.append(" ")
    lines.append("Items:")
    for item in invoice.get("items", []):
        lines.append(
            f"- {item['qty']} x {item['description']}: "
            f"{_format_currency(item['line_total_cents'], invoice['currency'])}"
        )
    lines.append(" ")
    lines.append(f"Subtotal: {_format_currency(invoice['subtotal_cents'], invoice['currency'])}")
    lines.append(f"Tax: {_format_currency(invoice['tax_cents'], invoice['currency'])}")
    lines.append(f"Total: {_format_currency(invoice['total_cents'], invoice['currency'])}")
    if invoice.get("balance_due_cents") is not None:
        lines.append(
            f"Balance Due: {_format_currency(invoice['balance_due_cents'], invoice['currency'])}"
        )
    if invoice.get("notes"):
        lines.append(" ")
        lines.append("Notes:")
        lines.append(str(invoice["notes"]))
    return _build_pdf(lines)


@router.get(
    "/i/{token}.pdf",
    response_class=Response,
    name="public_invoice_pdf",
)
async def download_invoice_pdf(
    token: str, session: AsyncSession = Depends(get_db_session)
) -> Response:
    invoice = await invoice_service.get_invoice_by_public_token(session, token)
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status == invoice_statuses.INVOICE_STATUS_VOID:
        raise HTTPException(status_code=400, detail="Invoice is void")
    lead = await invoice_service.fetch_customer(session, invoice)
    context = invoice_service.build_public_invoice_view(invoice, lead)
    pdf_bytes = _render_invoice_pdf(context)
    filename = f"{invoice.invoice_number}.pdf"
    headers = {"Content-Disposition": f"inline; filename=\"{filename}\""}
    return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)


@router.get("/i/{token}", response_class=HTMLResponse, name="public_invoice_view")
async def view_invoice(
    token: str, session: AsyncSession = Depends(get_db_session)
) -> HTMLResponse:
    invoice = await invoice_service.get_invoice_by_public_token(session, token)
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    lead = await invoice_service.fetch_customer(session, invoice)
    context = invoice_service.build_public_invoice_view(invoice, lead)
    context["token"] = token
    html = _render_invoice_html(context)
    return HTMLResponse(content=html)


@router.post(
    "/i/{token}/pay",
    response_model=invoice_schemas.InvoicePaymentInitResponse,
    status_code=status.HTTP_201_CREATED,
    name="public_invoice_pay",
)
async def create_invoice_payment(
    token: str,
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> invoice_schemas.InvoicePaymentInitResponse:
    invoice = await invoice_service.get_invoice_by_public_token(session, token)
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status == invoice_statuses.INVOICE_STATUS_VOID:
        raise HTTPException(status_code=409, detail="Invoice is void")
    if invoice.status == invoice_statuses.INVOICE_STATUS_DRAFT:
        raise HTTPException(status_code=409, detail="Invoice not sent yet")
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Stripe not configured")

    outstanding = invoice_service.outstanding_balance_cents(invoice)
    if outstanding <= 0:
        raise HTTPException(status_code=409, detail="Invoice already paid")

    lead = await invoice_service.fetch_customer(session, invoice)
    stripe_client = stripe_infra.resolve_client(http_request.app.state)
    checkout_session = stripe_infra.create_checkout_session(
        stripe_client=stripe_client,
        secret_key=settings.stripe_secret_key,
        amount_cents=outstanding,
        currency=invoice.currency,
        success_url=settings.stripe_invoice_success_url.replace("{INVOICE_ID}", invoice.invoice_id),
        cancel_url=settings.stripe_invoice_cancel_url.replace("{INVOICE_ID}", invoice.invoice_id),
        metadata={"invoice_id": invoice.invoice_id, "invoice_number": invoice.invoice_number},
        payment_intent_metadata={"invoice_id": invoice.invoice_id, "invoice_number": invoice.invoice_number},
        product_name=f"Invoice {invoice.invoice_number}",
        customer_email=getattr(lead, "email", None),
    )
    checkout_url = getattr(checkout_session, "url", None) or checkout_session.get("url")
    logger.info(
        "stripe_invoice_checkout_created",
        extra={
            "extra": {
                "invoice_id": invoice.invoice_id,
                "checkout_session_id": getattr(checkout_session, "id", None) or checkout_session.get("id"),
            }
        },
    )

    return invoice_schemas.InvoicePaymentInitResponse(
        provider="stripe",
        amount_cents=outstanding,
        currency=invoice.currency,
        checkout_url=checkout_url,
        client_secret=None,
    )
