from __future__ import annotations

import hashlib
import asyncio
import hmac
import logging
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import secrets
import uuid

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
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
from app.domain.invoices import schemas as invoice_schemas
from app.domain.invoices.schemas import InvoiceItemCreate
from app.domain.queues.schemas import QuickActionItem
from app.domain.leads.db_models import Lead
from app.settings import settings


logger = logging.getLogger(__name__)

_SQLITE_INVOICE_NUMBER_LOCK = asyncio.Lock()


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
            async with _SQLITE_INVOICE_NUMBER_LOCK:
                await session.execute(upsert)
                seq_stmt = select(InvoiceNumberSequence.last_number).where(
                    InvoiceNumberSequence.year == issue_date.year
                )
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

    # Set base_charge_cents on first invoice creation (if not already set by refunds)
    if order.base_charge_cents == 0 and order.refund_total_cents == 0:
        order.base_charge_cents = subtotal

    await session.flush()
    return invoice


def _paid_cents(invoice: Invoice) -> int:
    return sum(payment.amount_cents for payment in invoice.payments if payment.status == statuses.PAYMENT_STATUS_SUCCEEDED)


def outstanding_balance_cents(invoice: Invoice) -> int:
    return max(invoice.total_cents - _paid_cents(invoice), 0)


def recalculate_totals(invoice: Invoice) -> None:
    subtotal = 0
    tax_total = 0
    for item in invoice.items:
        subtotal += item.line_total_cents
        tax_total += _calculate_tax(item.line_total_cents, item.tax_rate)

    invoice.subtotal_cents = subtotal
    invoice.tax_cents = tax_total
    invoice.total_cents = subtotal + tax_total


async def _refresh_invoice_payment_status(session: AsyncSession, invoice: Invoice) -> int:
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
    return paid_amount


async def register_payment(
    session: AsyncSession,
    invoice: Invoice,
    *,
    provider: str,
    method: str,
    amount_cents: int,
    currency: str,
    status: str,
    received_at: datetime | None = None,
    reference: str | None = None,
    provider_ref: str | None = None,
    checkout_session_id: str | None = None,
    payment_intent_id: str | None = None,
) -> Payment | None:
    if invoice.status == statuses.INVOICE_STATUS_VOID:
        raise ValueError("Cannot record payments on a void invoice")
    if amount_cents <= 0:
        raise ValueError("Payment amount must be positive")

    existing_payment: Payment | None = None
    if provider_ref:
        existing_payment = await session.scalar(
            select(Payment)
            .where(Payment.provider == provider, Payment.provider_ref == provider_ref)
            .with_for_update(of=Payment)
        )
    if existing_payment is None and checkout_session_id:
        existing_payment = await session.scalar(
            select(Payment)
            .where(
                Payment.invoice_id == invoice.invoice_id,
                Payment.checkout_session_id == checkout_session_id,
            )
            .with_for_update(of=Payment)
        )

    if existing_payment:
        if provider_ref and not existing_payment.provider_ref:
            existing_payment.provider_ref = provider_ref
        existing_payment.status = status
        existing_payment.amount_cents = amount_cents
        existing_payment.currency = currency.upper()
        existing_payment.received_at = received_at or existing_payment.received_at
        existing_payment.reference = reference or existing_payment.reference
        existing_payment.payment_intent_id = (
            payment_intent_id or provider_ref or existing_payment.payment_intent_id
        )
        existing_payment.checkout_session_id = checkout_session_id or existing_payment.checkout_session_id
        await _refresh_invoice_payment_status(session, invoice)
        await session.flush()
        return existing_payment

    payment = Payment(
        invoice_id=invoice.invoice_id,
        provider=provider,
        provider_ref=provider_ref,
        checkout_session_id=checkout_session_id,
        payment_intent_id=payment_intent_id or provider_ref,
        method=method,
        amount_cents=amount_cents,
        currency=currency.upper(),
        status=status,
        received_at=received_at,
        reference=reference,
    )
    try:
        async with session.begin_nested():
            session.add(payment)
            await session.flush()
    except IntegrityError:
        logger.info(
            "invoice_payment_duplicate",
            extra={"extra": {"invoice_id": invoice.invoice_id, "provider": provider, "provider_ref": provider_ref}},
        )
        return None

    await _refresh_invoice_payment_status(session, invoice)
    await session.flush()
    return payment


async def record_stripe_payment(
    session: AsyncSession,
    invoice: Invoice,
    *,
    amount_cents: int,
    currency: str,
    status: str,
    provider_ref: str | None,
    reference: str | None,
    received_at: datetime,
    checkout_session_id: str | None = None,
    payment_intent_id: str | None = None,
) -> Payment | None:
    normalized_status = status.upper()
    normalized_currency = currency.upper()
    if normalized_status not in statuses.PAYMENT_STATUSES:
        raise ValueError("Invalid payment status")
    if normalized_currency != invoice.currency.upper():
        raise ValueError("Payment currency does not match invoice")

    return await register_payment(
        session,
        invoice,
        provider="stripe",
        provider_ref=provider_ref,
        method=statuses.PAYMENT_METHOD_CARD,
        amount_cents=amount_cents,
        currency=normalized_currency,
        status=normalized_status,
        received_at=received_at,
        reference=reference,
        checkout_session_id=checkout_session_id,
        payment_intent_id=payment_intent_id or provider_ref,
    )


async def record_manual_payment(
    session: AsyncSession,
    invoice: Invoice,
    amount_cents: int,
    method: str,
    reference: str | None = None,
    received_at: datetime | None = None,
) -> Payment:
    normalized_method = method.lower()
    if normalized_method not in statuses.PAYMENT_METHODS:
        raise ValueError("Invalid payment method")

    payment = await register_payment(
        session,
        invoice,
        provider="manual",
        provider_ref=None,
        method=normalized_method,
        amount_cents=amount_cents,
        currency=invoice.currency,
        status=statuses.PAYMENT_STATUS_SUCCEEDED,
        received_at=received_at or datetime.now(tz=timezone.utc),
        reference=reference,
    )
    if payment is None:
        raise ValueError("Payment was not recorded")
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
                "provider_ref": payment.provider_ref,
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
    return secrets.token_urlsafe(32)


def _public_token_secret() -> str:
    secret = getattr(settings, "invoice_public_token_secret", None)
    if secret:
        return secret
    fallback = getattr(settings, "app_name", "cleaning")
    logger.debug("using_fallback_public_token_secret", extra={"extra": {"fallback": fallback}})
    return fallback


def hash_public_token(token: str) -> str:
    secret = _public_token_secret().encode("utf-8")
    return hmac.new(secret, token.encode("utf-8"), hashlib.sha256).hexdigest()


async def upsert_public_token(
    session: AsyncSession, invoice: Invoice, *, mark_sent: bool = False
) -> str:
    token = generate_public_token()
    token_hash = hash_public_token(token)
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
    token_hash = hash_public_token(token)
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


async def list_invoice_reconcile_items(
    session: AsyncSession, org_id: uuid.UUID, *, include_all: bool = False
) -> list[invoice_schemas.InvoiceReconcileItem]:
    payments_subquery = (
        select(
            Payment.invoice_id.label("invoice_id"),
            func.count(Payment.payment_id)
            .filter(Payment.status == statuses.PAYMENT_STATUS_SUCCEEDED)
            .label("succeeded_payments_count"),
            func.coalesce(
                func.sum(Payment.amount_cents)
                .filter(Payment.status == statuses.PAYMENT_STATUS_SUCCEEDED),
                0,
            ).label("succeeded_amount_cents"),
            func.max(Payment.received_at).label("last_payment_at"),
        )
        .where(Payment.invoice_id.is_not(None), Payment.org_id == org_id)
        .group_by(Payment.invoice_id)
    ).subquery()

    succeeded_count = func.coalesce(payments_subquery.c.succeeded_payments_count, 0)
    succeeded_amount = func.coalesce(payments_subquery.c.succeeded_amount_cents, 0)
    outstanding_expr = case(
        (Invoice.total_cents - succeeded_amount < 0, 0),
        else_=Invoice.total_cents - succeeded_amount,
    ).label("outstanding_cents")

    stmt = (
        select(
            Invoice.invoice_id,
            Invoice.invoice_number,
            Invoice.status,
            Invoice.total_cents,
            outstanding_expr,
            succeeded_count.label("succeeded_payments_count"),
            payments_subquery.c.last_payment_at,
        )
        .outerjoin(payments_subquery, payments_subquery.c.invoice_id == Invoice.invoice_id)
        .where(Invoice.org_id == org_id)
        .order_by(Invoice.created_at.desc())
    )

    mismatch_condition = or_(
        and_(Invoice.status != statuses.INVOICE_STATUS_PAID, succeeded_count >= 1),
        and_(Invoice.status == statuses.INVOICE_STATUS_PAID, succeeded_count == 0),
        succeeded_count > 1,
    )

    if not include_all:
        stmt = stmt.where(mismatch_condition)

    result = await session.execute(stmt)
    cases: list[invoice_schemas.InvoiceReconcileItem] = []
    for row in result.all():
        cases.append(
            invoice_schemas.InvoiceReconcileItem(
                invoice_id=row.invoice_id,
                invoice_number=row.invoice_number,
                status=row.status,
                total_cents=int(row.total_cents),
                outstanding_cents=int(row.outstanding_cents or 0),
                succeeded_payments_count=int(row.succeeded_payments_count or 0),
                last_payment_at=row.last_payment_at,
                quick_actions=[
                    QuickActionItem(
                        label="Reconcile",
                        target=f"/v1/admin/finance/reconcile/invoices/{row.invoice_id}",
                        method="POST",
                    )
                ],
            )
        )

    return cases
