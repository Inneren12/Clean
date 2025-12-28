from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.bookings.db_models import Booking
from app.domain.invoices import statuses
from app.domain.invoices.db_models import Invoice, InvoiceItem, InvoiceNumberSequence, Payment
from app.domain.invoices.schemas import InvoiceItemCreate


def _calculate_tax(line_total_cents: int, tax_rate: float | None) -> int:
    if not tax_rate:
        return 0
    quantized = (Decimal(line_total_cents) * Decimal(tax_rate)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(quantized)


async def generate_invoice_number(session: AsyncSession, issue_date: date) -> str:
    bind = session.get_bind()
    dialect = bind.dialect.name if bind else "sqlite"
    insert_fn = pg_insert if dialect == "postgresql" else sqlite_insert
    stmt = insert_fn(InvoiceNumberSequence).values(year=issue_date.year, last_number=1)
    upsert = stmt.on_conflict_do_update(
        index_elements=[InvoiceNumberSequence.year],
        set_={"last_number": InvoiceNumberSequence.last_number + 1},
    ).returning(InvoiceNumberSequence.last_number)
    result = await session.execute(upsert)
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

    payment = Payment(
        invoice_id=invoice.invoice_id,
        provider="manual",
        method=method.lower(),
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
