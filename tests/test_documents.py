import datetime

import pytest
import sqlalchemy as sa

from app.domain.bookings.db_models import Booking
from app.domain.documents.db_models import Document
from app.domain.invoices import service as invoice_service, statuses
from app.domain.invoices.db_models import Invoice, Payment
from app.domain.invoices.schemas import InvoiceItemCreate
from app.domain.leads.db_models import Lead


def _lead_payload(name: str = "Document Lead") -> dict:
    return {
        "name": name,
        "phone": "780-555-1234",
        "email": "lead@example.com",
        "postal_code": "T5A",
        "address": "1 Test St",
        "preferred_dates": ["Mon"],
        "structured_inputs": {"beds": 1, "baths": 1, "cleaning_type": "standard"},
        "estimate_snapshot": {
            "price_cents": 12000,
            "subtotal_cents": 12000,
            "tax_cents": 0,
            "pricing_config_version": "v1",
            "config_hash": "hash",
            "line_items": [],
        },
        "pricing_config_version": "v1",
        "config_hash": "hash",
    }


async def _seed_invoice(async_session_maker) -> tuple[str, str, str]:
    async with async_session_maker() as session:
        lead = Lead(**_lead_payload())
        session.add(lead)
        await session.flush()
        booking = Booking(
            team_id=1,
            lead_id=lead.lead_id,
            starts_at=datetime.datetime.now(tz=datetime.timezone.utc),
            duration_minutes=90,
            status="PENDING",
        )
        session.add(booking)
        invoice = await invoice_service.create_invoice_from_order(
            session,
            order=booking,
            items=[InvoiceItemCreate(description="Cleaning", qty=1, unit_price_cents=15000)],
            issue_date=datetime.date.today(),
        )
        await session.flush()
        token = await invoice_service.upsert_public_token(session, invoice)
        await session.commit()
        return invoice.invoice_id, booking.booking_id, token


@pytest.mark.anyio
async def test_invoice_pdf_snapshot_is_immutable(client, async_session_maker):
    invoice_id, booking_id, token = await _seed_invoice(async_session_maker)

    first_resp = client.get(f"/i/{token}.pdf")
    assert first_resp.status_code == 200
    first_pdf = first_resp.content

    async with async_session_maker() as session:
        invoice = await session.get(Invoice, invoice_id)
        invoice.notes = "Changed after issue"
        await session.commit()

    second_resp = client.get(f"/i/{token}.pdf")
    assert second_resp.status_code == 200
    assert second_resp.content == first_pdf

    async with async_session_maker() as session:
        result = await session.execute(
            sa.select(Document).where(Document.reference_id == invoice_id, Document.document_type == "invoice")
        )
        document = result.scalar_one()
        assert document.template_version == 1
        assert document.reference_id == invoice_id


@pytest.mark.anyio
async def test_receipt_pdf_uses_snapshot(client, async_session_maker):
    invoice_id, _, token = await _seed_invoice(async_session_maker)

    async with async_session_maker() as session:
        invoice = await session.get(Invoice, invoice_id)
        payment = await invoice_service.register_payment(
            session,
            invoice,
            provider="manual",
            method=statuses.PAYMENT_METHOD_CASH,
            amount_cents=5000,
            currency=invoice.currency,
            status=statuses.PAYMENT_STATUS_SUCCEEDED,
        )
        await session.commit()
        payment_id = payment.payment_id

    first_resp = client.get(f"/i/{token}/receipts/{payment_id}.pdf")
    assert first_resp.status_code == 200
    first_pdf = first_resp.content

    async with async_session_maker() as session:
        payment = await session.get(Payment, payment_id)
        payment.reference = "UPDATED"
        await session.commit()

    second_resp = client.get(f"/i/{token}/receipts/{payment_id}.pdf")
    assert second_resp.status_code == 200
    assert second_resp.content == first_pdf


@pytest.mark.anyio
async def test_service_agreement_downloadable(client, async_session_maker):
    invoice_id, booking_id, token = await _seed_invoice(async_session_maker)

    resp = client.get(f"/i/{token}/service-agreement.pdf")
    assert resp.status_code == 200
    assert resp.content

    async with async_session_maker() as session:
        result = await session.execute(
            sa.select(Document).where(
                Document.reference_id == booking_id, Document.document_type == "service_agreement"
            )
        )
        document = result.scalar_one()
        assert document.document_type == "service_agreement"
        assert document.template_version == 1
