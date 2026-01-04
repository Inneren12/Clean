from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.bookings import photos_service
from app.domain.bookings.db_models import Booking, OrderPhoto
from app.domain.data_rights.db_models import DataDeletionRequest
from app.domain.errors import DomainError
from app.domain.invoices.db_models import Invoice, InvoicePublicToken, Payment
from app.domain.leads.db_models import Lead
from app.domain.leads.service import export_payload_from_lead
from app.infra.storage import StorageBackend, new_storage_backend


def _sanitize_booking(booking: Booking) -> dict:
    return {
        "booking_id": booking.booking_id,
        "lead_id": booking.lead_id,
        "team_id": booking.team_id,
        "status": booking.status,
        "starts_at": booking.starts_at.isoformat() if booking.starts_at else None,
        "duration_minutes": booking.duration_minutes,
        "scheduled_date": booking.scheduled_date.isoformat() if booking.scheduled_date else None,
        "created_at": booking.created_at.isoformat() if booking.created_at else None,
        "updated_at": booking.updated_at.isoformat() if booking.updated_at else None,
        "deposit_required": booking.deposit_required,
        "deposit_cents": booking.deposit_cents,
        "deposit_status": booking.deposit_status,
        "consent_photos": booking.consent_photos,
    }


def _sanitize_invoice(invoice: Invoice) -> dict:
    return {
        "invoice_id": invoice.invoice_id,
        "invoice_number": invoice.invoice_number,
        "customer_id": invoice.customer_id,
        "order_id": invoice.order_id,
        "status": invoice.status,
        "issue_date": invoice.issue_date.isoformat() if invoice.issue_date else None,
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "currency": invoice.currency,
        "subtotal_cents": invoice.subtotal_cents,
        "taxable_subtotal_cents": invoice.taxable_subtotal_cents,
        "tax_cents": invoice.tax_cents,
        "tax_rate_basis": float(invoice.tax_rate_basis) if invoice.tax_rate_basis else None,
        "total_cents": invoice.total_cents,
        "notes": invoice.notes,
        "created_by": invoice.created_by,
        "created_at": invoice.created_at.isoformat() if invoice.created_at else None,
        "updated_at": invoice.updated_at.isoformat() if invoice.updated_at else None,
    }


def _sanitize_payment(payment: Payment) -> dict:
    return {
        "payment_id": payment.payment_id,
        "invoice_id": payment.invoice_id,
        "booking_id": payment.booking_id,
        "provider": payment.provider,
        "method": payment.method,
        "amount_cents": payment.amount_cents,
        "currency": payment.currency,
        "status": payment.status,
        "received_at": payment.received_at.isoformat() if payment.received_at else None,
        "reference": payment.reference,
        "created_at": payment.created_at.isoformat() if payment.created_at else None,
    }


def _photo_reference(photo: OrderPhoto) -> dict:
    return {
        "photo_id": photo.photo_id,
        "order_id": photo.order_id,
        "filename": photo.filename,
        "original_filename": photo.original_filename,
        "content_type": photo.content_type,
        "size_bytes": photo.size_bytes,
        "storage_provider": photo.storage_provider,
        "storage_key": photo.storage_key,
        "review_status": photo.review_status,
        "needs_retake": photo.needs_retake,
        "created_at": photo.created_at.isoformat() if photo.created_at else None,
    }


async def export_client_data(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    lead_id: str | None = None,
    email: str | None = None,
) -> dict:
    lead_filters = [Lead.org_id == org_id]
    if lead_id:
        lead_filters.append(Lead.lead_id == lead_id)
    if email:
        lead_filters.append(Lead.email == email)

    lead_result = await session.execute(select(Lead).where(*lead_filters))
    leads = list(lead_result.scalars().all())
    if not leads:
        raise DomainError(detail="lead_not_found")

    lead_ids = [lead.lead_id for lead in leads]
    booking_result = await session.execute(
        select(Booking).where(Booking.lead_id.in_(lead_ids), Booking.org_id == org_id)
    )
    bookings = list(booking_result.scalars().all())
    booking_ids = [booking.booking_id for booking in bookings]

    invoice_result = await session.execute(
        select(Invoice).where(
            Invoice.org_id == org_id,
            (Invoice.customer_id.in_(lead_ids)) | (Invoice.order_id.in_(booking_ids)),
        )
    )
    invoices = list(invoice_result.scalars().all())
    invoice_ids = [invoice.invoice_id for invoice in invoices]

    payment_result = await session.execute(
        select(Payment).where(
            Payment.org_id == org_id,
            (Payment.invoice_id.in_(invoice_ids)) | (Payment.booking_id.in_(booking_ids)),
        )
    )
    payments = list(payment_result.scalars().all())

    photos_result = await session.execute(
        select(OrderPhoto).where(
            OrderPhoto.org_id == org_id, OrderPhoto.order_id.in_(booking_ids)
        )
    )
    photos = list(photos_result.scalars().all())

    return {
        "leads": [export_payload_from_lead(lead) for lead in leads],
        "bookings": [_sanitize_booking(booking) for booking in bookings],
        "invoices": [_sanitize_invoice(invoice) for invoice in invoices],
        "payments": [_sanitize_payment(payment) for payment in payments],
        "photos": [_photo_reference(photo) for photo in photos],
    }


async def request_data_deletion(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    lead_id: str | None,
    email: str | None,
    reason: str | None,
    requested_by: str | None,
) -> tuple[DataDeletionRequest, int]:
    lead_filters = [Lead.org_id == org_id]
    if lead_id:
        lead_filters.append(Lead.lead_id == lead_id)
    if email:
        lead_filters.append(Lead.email == email)

    lead_result = await session.execute(select(Lead).where(*lead_filters))
    leads = list(lead_result.scalars().all())
    if not leads:
        raise DomainError(detail="lead_not_found")

    now = datetime.now(tz=timezone.utc)
    for lead in leads:
        lead.pending_deletion = True
        lead.deletion_requested_at = now

    request = DataDeletionRequest(
        org_id=org_id,
        lead_id=lead_id,
        email=email,
        reason=reason,
        requested_by=requested_by,
        status="pending",
        processed_notes=None,
    )
    session.add(request)
    await session.flush()
    return request, len(leads)


async def _anonymize_lead(
    session: AsyncSession,
    lead: Lead,
    *,
    storage: StorageBackend,
) -> tuple[int, int]:
    booking_result = await session.execute(
        select(Booking).where(Booking.lead_id == lead.lead_id, Booking.org_id == lead.org_id)
    )
    bookings = list(booking_result.scalars().all())
    booking_ids = [booking.booking_id for booking in bookings]
    photo_count = 0
    for booking_id in booking_ids:
        photo_result = await session.execute(
            select(OrderPhoto).where(
                OrderPhoto.order_id == booking_id,
                OrderPhoto.org_id == lead.org_id,
            )
        )
        photos = list(photo_result.scalars().all())
        for photo in photos:
            await photos_service.delete_photo(
                session,
                booking_id,
                photo.photo_id,
                storage=storage,
                org_id=lead.org_id,
            )
            photo_count += 1

    if booking_ids:
        await session.execute(
            update(Booking)
            .where(Booking.booking_id.in_(booking_ids))
            .values(lead_id=None)
        )

    invoice_result = await session.execute(
        select(Invoice).where(
            Invoice.org_id == lead.org_id,
            (Invoice.customer_id == lead.lead_id) | (Invoice.order_id.in_(booking_ids)),
        )
    )
    invoices = list(invoice_result.scalars().all())
    invoice_ids = [invoice.invoice_id for invoice in invoices]
    invoices_detached = 0
    if invoice_ids:
        await session.execute(delete(InvoicePublicToken).where(InvoicePublicToken.invoice_id.in_(invoice_ids)))
        detach = await session.execute(
            update(Invoice)
            .where(Invoice.invoice_id.in_(invoice_ids))
            .values(customer_id=None)
        )
        invoices_detached = detach.rowcount or 0

    lead.name = "Deleted contact"
    lead.phone = "deleted"
    lead.email = None
    lead.postal_code = None
    lead.address = None
    lead.access_notes = None
    lead.parking = None
    lead.pets = None
    lead.allergies = None
    lead.notes = None
    lead.structured_inputs = {}
    lead.estimate_snapshot = {}
    lead.preferred_dates = []
    lead.pending_deletion = False
    lead.deleted_at = datetime.now(tz=timezone.utc)

    return photo_count, invoices_detached


async def process_pending_deletions(
    session: AsyncSession,
    *,
    storage_backend: StorageBackend | None = None,
) -> dict[str, int]:
    storage = storage_backend or getattr(session.bind, "storage_backend", None) or new_storage_backend()
    stmt = select(DataDeletionRequest).where(DataDeletionRequest.status == "pending")
    result = await session.execute(stmt)
    requests = list(result.scalars().all())
    processed = 0
    leads_anonymized = 0
    photos_deleted = 0
    invoices_detached = 0

    for request in requests:
        lead_filters = [Lead.org_id == request.org_id]
        if request.lead_id:
            lead_filters.append(Lead.lead_id == request.lead_id)
        if request.email:
            lead_filters.append(Lead.email == request.email)

        lead_result = await session.execute(select(Lead).where(*lead_filters))
        leads = list(lead_result.scalars().all())
        for lead in leads:
            photo_count, detached = await _anonymize_lead(session, lead, storage=storage)
            leads_anonymized += 1
            photos_deleted += photo_count
            invoices_detached += detached

        request.status = "processed"
        request.processed_at = datetime.now(tz=timezone.utc)
        request.processed_notes = f"anonymized:{len(leads)}"
        processed += 1

    await session.commit()
    return {
        "processed": processed,
        "leads_anonymized": leads_anonymized,
        "photos_deleted": photos_deleted,
        "invoices_detached": invoices_detached,
    }
