import base64
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.domain.bookings.db_models import Booking
from app.domain.export_events.db_models import ExportEvent
from app.domain.invoices import statuses as invoice_statuses
from app.domain.invoices.db_models import Invoice
from app.domain.leads.db_models import Lead
from app.domain.saas import service as saas_service
from app.domain.saas.db_models import MembershipRole, Organization
from app.settings import settings

ORG_HEADER = "X-Test-Org"


def _auth_headers(username: str, password: str, org_id: uuid.UUID) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}", ORG_HEADER: str(org_id)}


@pytest.fixture()
def admin_credentials():
    original_username = settings.admin_basic_username
    original_password = settings.admin_basic_password
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"
    try:
        yield
    finally:
        settings.admin_basic_username = original_username
        settings.admin_basic_password = original_password


@pytest.fixture()
async def orgs(async_session_maker):
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    async with async_session_maker() as session:
        session.add_all([Organization(org_id=org_a, name="Org A"), Organization(org_id=org_b, name="Org B")])
        await session.commit()
    return org_a, org_b


@pytest.fixture()
def upload_root(tmp_path):
    original_root = settings.order_upload_root
    original_backend = settings.order_storage_backend
    from app.main import app

    original_backend_state = getattr(app.state, "storage_backend", None)
    settings.order_storage_backend = "local"
    settings.order_upload_root = str(tmp_path)
    app.state.storage_backend = None
    try:
        yield tmp_path
    finally:
        settings.order_upload_root = original_root
        settings.order_storage_backend = original_backend
        app.state.storage_backend = original_backend_state


@pytest.mark.anyio
async def test_cross_org_finance_and_exports_blocked(client, async_session_maker, orgs, admin_credentials):
    org_a, org_b = orgs
    original_default_org = settings.default_org_id
    settings.default_org_id = org_a

    async with async_session_maker() as session:
        invoice = Invoice(
            org_id=org_a,
            invoice_number="INV-A",
            status=invoice_statuses.INVOICE_STATUS_SENT,
            issue_date=datetime.now(tz=timezone.utc).date(),
            currency="CAD",
            subtotal_cents=1000,
            tax_cents=0,
            total_cents=1000,
        )
        export_event = ExportEvent(
            org_id=org_a,
            mode="webhook",
            target_url="https://example.com/webhook",
            payload={"hello": "world"},
            attempts=1,
            last_error_code="500",
        )
        session.add_all([invoice, export_event])
        await session.commit()
        invoice_id = invoice.invoice_id
        event_id = export_event.event_id

    cross_headers = _auth_headers("admin", "secret", org_b)
    payment_attempt = client.post(
        f"/v1/admin/invoices/{invoice_id}/record-payment",
        headers=cross_headers,
        json={"amount_cents": 1000, "method": "cash"},
    )
    assert payment_attempt.status_code == 404

    export_replay = client.post(
        f"/v1/admin/export-dead-letter/{event_id}/replay",
        headers=cross_headers,
    )
    assert export_replay.status_code == 404

    settings.default_org_id = original_default_org


@pytest.mark.anyio
async def test_cross_org_iam_resets_forbidden(client, async_session_maker, orgs):
    org_a, org_b = orgs
    original_auth_secret = settings.auth_secret_key
    settings.auth_secret_key = "test-secret-key"

    async with async_session_maker() as session:
        org_a_obj = await session.get(Organization, org_a)
        org_b_obj = await session.get(Organization, org_b)
        admin_a = await saas_service.create_user(session, "admin-a@example.com", "SecretPass123!")
        admin_b = await saas_service.create_user(session, "admin-b@example.com", "SecretPass123!")
        worker = await saas_service.create_user(session, "worker@example.com", "WorkerPass123!")
        membership_admin_a = await saas_service.create_membership(session, org_a_obj, admin_a, MembershipRole.ADMIN)
        membership_admin_b = await saas_service.create_membership(session, org_b_obj, admin_b, MembershipRole.ADMIN)
        await saas_service.create_membership(session, org_a_obj, worker, MembershipRole.WORKER)
        await session.commit()

    admin_a_token = saas_service.build_access_token(admin_a, membership_admin_a)
    admin_b_token = saas_service.build_access_token(admin_b, membership_admin_b)

    cross_reset = client.post(
        f"/v1/iam/users/{worker.user_id}/reset-temp-password",
        json={"reason": "test"},
        headers={"Authorization": f"Bearer {admin_b_token}"},
    )
    assert cross_reset.status_code == 403

    allowed_reset = client.post(
        f"/v1/iam/users/{worker.user_id}/reset-temp-password",
        json={"reason": "test"},
        headers={"Authorization": f"Bearer {admin_a_token}"},
    )
    assert allowed_reset.status_code == 200
    assert allowed_reset.json()["must_change_password"] is True

    settings.auth_secret_key = original_auth_secret


@pytest.mark.anyio
async def test_signed_photo_urls_are_org_scoped(client, async_session_maker, orgs, admin_credentials, upload_root):
    org_a, org_b = orgs
    original_default_org = settings.default_org_id
    settings.default_org_id = org_a

    async with async_session_maker() as session:
        lead = Lead(
            org_id=org_a,
            name="Photo Lead",
            phone="780-555-2222",
            email="photo@example.com",
            postal_code="T5A",
            address="9 Camera St",
            preferred_dates=["Wed"],
            structured_inputs={"beds": 1, "baths": 1, "cleaning_type": "standard"},
            estimate_snapshot={
                "price_cents": 4000,
                "subtotal_cents": 4000,
                "tax_cents": 0,
                "pricing_config_version": "v1",
                "config_hash": "hash",
                "line_items": [],
            },
            pricing_config_version="v1",
            config_hash="hash",
        )
        booking = Booking(
            org_id=org_a,
            team_id=1,
            lead_id=lead.lead_id,
            starts_at=datetime.now(tz=timezone.utc) + timedelta(hours=4),
            duration_minutes=90,
            status="CONFIRMED",
            consent_photos=True,
        )
        session.add_all([lead, booking])
        await session.commit()
        booking_id = booking.booking_id

    files = {"file": ("after.jpg", b"hello-image", "image/jpeg")}
    upload = client.post(
        f"/v1/orders/{booking_id}/photos",
        data={"phase": "AFTER"},
        files=files,
        headers=_auth_headers("admin", "secret", org_a),
    )
    assert upload.status_code == 201
    photo_id = upload.json()["photo_id"]

    cross_org_headers = _auth_headers("admin", "secret", org_b)
    signed_cross_org = client.get(
        f"/v1/orders/{booking_id}/photos/{photo_id}/signed_url",
        headers=cross_org_headers,
    )
    assert signed_cross_org.status_code == 404

    signed = client.get(
        f"/v1/orders/{booking_id}/photos/{photo_id}/signed_url",
        headers=_auth_headers("admin", "secret", org_a),
    )
    assert signed.status_code == 200
    signed_url = signed.json()["url"]

    cross_org_download = client.get(
        f"/v1/orders/{booking_id}/photos/{photo_id}/signed-download",
        headers=cross_org_headers,
    )
    assert cross_org_download.status_code == 401

    fetch = client.get(signed_url)
    assert fetch.status_code == 200

    stored_files = list(Path(upload_root / "org" / str(org_a) / "bookings" / booking_id).glob("*"))
    assert stored_files, "file should be stored under the org namespace"

    settings.default_org_id = original_default_org
