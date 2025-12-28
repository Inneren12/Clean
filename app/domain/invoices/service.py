from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import secrets

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.bookings.db_models import Booking
from app.domain.invoices import statuses
from app.domain.invoices.db_models import (
    Invoice,
    InvoiceItem,
    InvoiceNumberSequence,
    InvoicePublicToken,
    Payment,
)
from app.domain.invoices.schemas import InvoiceItemCreate
from app.domain.leads.db_models import Lead


def _calculate_tax(line_total_cents: int, tax_rate: Decimal | float | None) -> int:
    if tax_rate is None:
        return 0
    rate_decimal = Decimal(str(tax_rate))
    quantized = (Decimal(line_total_cents) * rate_decimal).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(quantized)


async def generate_invoice_number(session: AsyncSession, issue_date: date) -> str:
    bind = session.get_bind()
    dialect = bind.dialect.name if bind else "sqlite"
    insert_fn = pg_insert if dialect == "postgresql" else sqlite_insert
    stmt = insert_fn(InvoiceNumberSequence).values(year=issue_date.year, last_number=1)
    upsert = stmt.on_conflict_do_update(
        index_elements=[InvoiceNumberSequence.year],
        set_={"last_number": InvoiceNumberSequence.last_number + 1},
    )

    number: int
    if dialect == "postgresql":
        result = await session.execute(upsert.returning(InvoiceNumberSequence.last_number))
        number = int(result.scalar_one())
    else:
        try:
            result = await session.execute(upsert.returning(InvoiceNumberSequence.last_number))
            number = int(result.scalar_one())
        except Exception:  # noqa: BLE001
            await session.execute(upsert)
            seq_stmt = select(InvoiceNumberSequence.last_number).where(InvoiceNumberSequence.year == issue_date.year)
            result = await session.execute(seq_stmt)
            number = int(result.scalar_one())
    return f"INV-{issue_date.year}-{number:06d}"


async def create_invoice_from_order(
    session: AsyncSession,
    order: Booking,
    items: list[InvoiceItemCreate],
    issue_date: date | None = None,
    due_date: date | None = None,
    currency: str = "CAD",
    notes: str | None = None,
    created_by: str | None = None,
) -> Invoice:
    if not items:
        raise ValueError("Invoice requires at least one item")

    issue = issue_date or date.today()
    invoice_number = await generate_invoice_number(session, issue)

    subtotal = 0
    tax_total = 0
    invoice_items: list[InvoiceItem] = []
    for payload in items:
        if payload.qty <= 0:
            raise ValueError("Quantity must be positive")
        if payload.unit_price_cents < 0:
            raise ValueError("Unit price must be non-negative")
        line_total = payload.qty * payload.unit_price_cents
        line_tax = _calculate_tax(line_total, payload.tax_rate)
        subtotal += line_total
        tax_total += line_tax
        invoice_items.append(
            InvoiceItem(
                description=payload.description,
                qty=payload.qty,
                unit_price_cents=payload.unit_price_cents,
                line_total_cents=line_total,
                tax_rate=payload.tax_rate,
            )
        )

    invoice = Invoice(
        invoice_number=invoice_number,
        order_id=order.booking_id,
        customer_id=order.lead_id,
        status=statuses.INVOICE_STATUS_DRAFT,
        issue_date=issue,
        due_date=due_date,
        currency=currency.upper(),
        subtotal_cents=subtotal,
        tax_cents=tax_total,
        total_cents=subtotal + tax_total,
        notes=notes,
        created_by=created_by,
    )
    invoice.items = invoice_items
    session.add(invoice)
    await session.flush()
    return invoice


def _paid_cents(invoice: Invoice) -> int:
    return sum(payment.amount_cents for payment in invoice.payments if payment.status == statuses.PAYMENT_STATUS_SUCCEEDED)


async def record_manual_payment(
    session: AsyncSession,
    invoice: Invoice,
    amount_cents: int,
    method: str,
    reference: str | None = None,
    received_at: datetime | None = None,
) -> Payment:
    if invoice.status == statuses.INVOICE_STATUS_VOID:
        raise ValueError("Cannot record payments on a void invoice")
    if amount_cents <= 0:
        raise ValueError("Payment amount must be positive")
    normalized_method = method.lower()
    if normalized_method not in statuses.PAYMENT_METHODS:
        raise ValueError("Invalid payment method")

    payment = Payment(
        invoice_id=invoice.invoice_id,
        provider="manual",
        method=normalized_method,
        amount_cents=amount_cents,
        currency=invoice.currency,
        status=statuses.PAYMENT_STATUS_SUCCEEDED,
        received_at=received_at or datetime.now(tz=timezone.utc),
        reference=reference,
    )
    session.add(payment)
    await session.flush()
    paid = await session.scalar(
        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
            Payment.invoice_id == invoice.invoice_id,
            Payment.status == statuses.PAYMENT_STATUS_SUCCEEDED,
        )
    )
    paid_amount = int(paid or 0)
    if paid_amount >= invoice.total_cents:
        invoice.status = statuses.INVOICE_STATUS_PAID
    elif paid_amount > 0:
        invoice.status = statuses.INVOICE_STATUS_PARTIAL

    await session.flush()
    return payment


def build_invoice_response(invoice: Invoice) -> dict:
    paid_cents = _paid_cents(invoice)
    return {
        "invoice_id": invoice.invoice_id,
        "invoice_number": invoice.invoice_number,
        "order_id": invoice.order_id,
        "customer_id": invoice.customer_id,
        "status": invoice.status,
        "issue_date": invoice.issue_date,
        "due_date": invoice.due_date,
        "currency": invoice.currency,
        "subtotal_cents": invoice.subtotal_cents,
        "tax_cents": invoice.tax_cents,
        "total_cents": invoice.total_cents,
        "paid_cents": paid_cents,
        "balance_due_cents": max(invoice.total_cents - paid_cents, 0),
        "notes": invoice.notes,
        "created_by": invoice.created_by,
        "created_at": invoice.created_at,
        "updated_at": invoice.updated_at,
        "items": [
            {
                "item_id": item.item_id,
                "description": item.description,
                "qty": item.qty,
                "unit_price_cents": item.unit_price_cents,
                "line_total_cents": item.line_total_cents,
                "tax_rate": float(item.tax_rate) if item.tax_rate is not None else None,
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
                "received_at": payment.received_at,
                "reference": payment.reference,
                "created_at": payment.created_at,
            }
            for payment in invoice.payments
        ],
    }


def build_invoice_list_item(invoice: Invoice) -> dict:
    paid_cents = _paid_cents(invoice)
    return {
        "invoice_id": invoice.invoice_id,
        "invoice_number": invoice.invoice_number,
        "order_id": invoice.order_id,
        "customer_id": invoice.customer_id,
        "status": invoice.status,
        "issue_date": invoice.issue_date,
        "due_date": invoice.due_date,
        "currency": invoice.currency,
        "subtotal_cents": invoice.subtotal_cents,
        "tax_cents": invoice.tax_cents,
        "total_cents": invoice.total_cents,
        "paid_cents": paid_cents,
        "balance_due_cents": max(invoice.total_cents - paid_cents, 0),
        "created_at": invoice.created_at,
        "updated_at": invoice.updated_at,
    }


def generate_public_token() -> str:
    # urlsafe -> avoids leaking format; 48 bytes ~ 64 chars
    return secrets.token_urlsafe(48)


def _hash_public_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def upsert_public_token(
    session: AsyncSession, invoice: Invoice, *, mark_sent: bool = False
) -> str:
    token = generate_public_token()
    token_hash = _hash_public_token(token)
    now = datetime.now(tz=timezone.utc)

    existing = await session.scalar(
        select(InvoicePublicToken).where(InvoicePublicToken.invoice_id == invoice.invoice_id)
    )
    if existing:
        existing.token_hash = token_hash
        existing.rotated_at = now
        if mark_sent:
            existing.last_sent_at = now
    else:
        record = InvoicePublicToken(
            invoice_id=invoice.invoice_id,
            token_hash=token_hash,
            created_at=now,
        )
        if mark_sent:
            record.last_sent_at = now
        session.add(record)

    await session.flush()
    return token


async def get_invoice_by_public_token(session: AsyncSession, token: str) -> Invoice | None:
    token_hash = _hash_public_token(token)
    stmt = (
        select(Invoice)
        .join(InvoicePublicToken, InvoicePublicToken.invoice_id == Invoice.invoice_id)
        .options(selectinload(Invoice.items), selectinload(Invoice.payments))
        .where(InvoicePublicToken.token_hash == token_hash)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def fetch_customer(session: AsyncSession, invoice: Invoice) -> Lead | None:
    if not invoice.customer_id:
        return None
    return await session.get(Lead, invoice.customer_id)


def build_public_invoice_view(invoice: Invoice, lead: Lead | None) -> dict:
    invoice_data = build_invoice_response(invoice)
    customer = {
        "name": getattr(lead, "name", None),
        "email": getattr(lead, "email", None),
        "address": getattr(lead, "address", None),
    }
    return {"invoice": invoice_data, "customer": customer}
