import logging
from datetime import datetime, timezone

from sqlalchemy import and_, exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.bookings.db_models import Booking, EmailEvent
from app.domain.invoices import service as invoice_service
from app.domain.invoices import statuses as invoice_statuses
from app.domain.invoices.db_models import Invoice
from app.domain.leads.db_models import Lead
from app.domain.notifications import email_service
from app.domain.nps import service as nps_service
from app.domain.nps.db_models import NpsResponse
from app.infra.email import EmailAdapter
from app.settings import settings

logger = logging.getLogger(__name__)


async def run_booking_reminders(session: AsyncSession, adapter: EmailAdapter | None) -> dict[str, int]:
    return await email_service.scan_and_send_reminders(session, adapter)


async def run_email_dlq(session: AsyncSession, adapter: EmailAdapter | None) -> dict[str, int]:
    return await email_service.retry_email_failures(session, adapter)


def _public_base_url(explicit: str | None = None) -> str | None:
    base = explicit or settings.public_base_url or settings.client_portal_base_url
    if not base:
        return None
    return base.rstrip("/")


async def _invoice_public_links(session: AsyncSession, invoice: Invoice, base_url: str) -> tuple[str, str]:
    token = await invoice_service.upsert_public_token(session, invoice, mark_sent=True)
    public_link = f"{base_url}/i/{token}"
    public_link_pdf = f"{public_link}.pdf"
    return public_link, public_link_pdf


async def run_invoice_notifications(
    session: AsyncSession,
    adapter: EmailAdapter | None,
    *,
    base_url: str | None = None,
) -> dict[str, int]:
    if adapter is None:
        logger.warning("invoice_email_adapter_missing")
    link_base = _public_base_url(base_url)
    if not link_base:
        logger.warning("invoice_public_base_url_missing")
        return {"sent": 0, "overdue": 0}

    sent_total = 0
    overdue_total = 0

    sent_exists = exists().where(
        and_(
            EmailEvent.invoice_id == Invoice.invoice_id,
            EmailEvent.email_type == email_service.EMAIL_TYPE_INVOICE_SENT,
        )
    )

    overdue_exists = exists().where(
        and_(
            EmailEvent.invoice_id == Invoice.invoice_id,
            EmailEvent.email_type == email_service.EMAIL_TYPE_INVOICE_OVERDUE,
        )
    )

    sent_stmt = (
        select(Invoice)
        .options(selectinload(Invoice.payments))
        .where(
            Invoice.status == invoice_statuses.INVOICE_STATUS_SENT,
            ~sent_exists.correlate(Invoice),
        )
    )
    result = await session.execute(sent_stmt)
    for invoice in result.scalars():
        lead = await invoice_service.fetch_customer(session, invoice)
        if not lead or not lead.email:
            continue
        public_link, pdf_link = await _invoice_public_links(session, invoice, link_base)
        delivered = await email_service.send_invoice_sent_email(
            session,
            adapter,
            invoice,
            lead,
            public_link=public_link,
            public_link_pdf=pdf_link,
        )
        if delivered:
            sent_total += 1

    today = datetime.now(tz=timezone.utc).date()
    overdue_stmt = (
        select(Invoice)
        .options(selectinload(Invoice.payments))
        .where(
            Invoice.due_date.isnot(None),
            Invoice.due_date < today,
            Invoice.status.in_(
                {
                    invoice_statuses.INVOICE_STATUS_SENT,
                    invoice_statuses.INVOICE_STATUS_PARTIAL,
                    invoice_statuses.INVOICE_STATUS_OVERDUE,
                }
            ),
            sent_exists.correlate(Invoice),
            ~overdue_exists.correlate(Invoice),
        )
    )
    overdue_result = await session.execute(overdue_stmt)
    for invoice in overdue_result.scalars():
        lead = await invoice_service.fetch_customer(session, invoice)
        if not lead or not lead.email:
            continue
        invoice.balance_due_cents = invoice_service.outstanding_balance_cents(invoice)
        public_link, _pdf_link = await _invoice_public_links(session, invoice, link_base)
        delivered = await email_service.send_invoice_overdue_email(
            session, adapter, invoice, lead, public_link=public_link
        )
        if delivered:
            overdue_total += 1

    return {"sent": sent_total, "overdue": overdue_total}


async def run_nps_sends(
    session: AsyncSession,
    adapter: EmailAdapter | None,
    *,
    base_url: str | None = None,
) -> dict[str, int]:
    base = _public_base_url(base_url)
    if not base:
        logger.warning("nps_public_base_url_missing")
        return {"sent": 0}

    sent_exists = exists().where(
        and_(
            EmailEvent.booking_id == Booking.booking_id,
            EmailEvent.email_type == email_service.EMAIL_TYPE_NPS_SURVEY,
        )
    )
    response_exists = exists().where(NpsResponse.order_id == Booking.booking_id)

    stmt = (
        select(Booking, Lead)
        .join(Lead, Lead.lead_id == Booking.lead_id)
        .where(
            Booking.status == "DONE",
            Lead.email.isnot(None),
            ~sent_exists.correlate(Booking),
            ~response_exists.correlate(Booking),
        )
    )
    result = await session.execute(stmt)

    sent = 0
    for booking, lead in result.all():
        token = nps_service.issue_nps_token(
            booking.booking_id,
            client_id=booking.client_id,
            email=lead.email,
            secret=settings.client_portal_secret,
        )
        survey_link = f"{base}/nps/{booking.booking_id}?token={token}"
        delivered = await email_service.send_nps_survey_email(
            session,
            adapter,
            booking,
            lead,
            survey_link,
        )
        if delivered:
            sent += 1

    return {"sent": sent}
