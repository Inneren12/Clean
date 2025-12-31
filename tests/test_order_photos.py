import asyncio
import base64
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.domain.saas import billing_service, service as saas_service
from app.domain.saas.db_models import OrganizationUsageEvent

from app.domain.bookings.db_models import Booking
from app.domain.leads.db_models import Lead
from app.main import app
from app.settings import settings


def _basic_auth_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


async def _create_booking(async_session_maker, consent: bool = False) -> str:
    async with async_session_maker() as session:
        lead = Lead(
            name="Photo Lead",
            phone="780-555-1111",
            email="photo@example.com",
            postal_code="T5A",
            address="123 Test St",
            preferred_dates=["Mon"],
            structured_inputs={"beds": 2, "baths": 1, "cleaning_type": "standard"},
            estimate_snapshot={
                "price_cents": 10000,
                "subtotal_cents": 10000,
                "tax_cents": 0,
                "pricing_config_version": "v1",
                "config_hash": "hash",
                "line_items": [],
            },
            pricing_config_version="v1",
            config_hash="hash",
        )
        session.add(lead)
        await session.commit()
        await session.refresh(lead)
        booking = Booking(
            team_id=1,
            lead_id=lead.lead_id,
            starts_at=datetime.now(tz=timezone.utc),
            duration_minutes=60,
            status="CONFIRMED",
            consent_photos=consent,
        )
        session.add(booking)
        await session.commit()
        await session.refresh(booking)
        return booking.booking_id


def _create_saas_token(async_session_maker):
    async def _inner():
        async with async_session_maker() as session:
            org = await saas_service.create_organization(session, "Photo Tenant")
            user = await saas_service.create_user(session, "photo-owner@example.com", "pw")
            membership = await saas_service.create_membership(
                session, org, user, saas_service.MembershipRole.OWNER
            )
            token = saas_service.build_access_token(user, membership)
            await session.commit()
            return token, org.org_id

    return asyncio.run(_inner())


@pytest.fixture()
def upload_root(tmp_path) -> Path:
    original_root = settings.order_upload_root
    settings.order_upload_root = str(tmp_path)
    app.state.storage_backend = None
    yield tmp_path
    settings.order_upload_root = original_root
    app.state.storage_backend = None


@pytest.fixture()
def owner_headers():
    original_owner_username = settings.owner_basic_username
    original_owner_password = settings.owner_basic_password
    settings.owner_basic_username = "owner"
    settings.owner_basic_password = "secret"
    yield _basic_auth_header("owner", "secret")
    settings.owner_basic_username = original_owner_username
    settings.owner_basic_password = original_owner_password


@pytest.fixture()
def admin_headers():
    original_admin_username = settings.admin_basic_username
    original_admin_password = settings.admin_basic_password
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"
    yield _basic_auth_header("admin", "secret")
    settings.admin_basic_username = original_admin_username
    settings.admin_basic_password = original_admin_password


@pytest.fixture()
def dispatcher_headers():
    original_dispatcher_username = settings.dispatcher_basic_username
    original_dispatcher_password = settings.dispatcher_basic_password
    settings.dispatcher_basic_username = "dispatcher"
    settings.dispatcher_basic_password = "secret"
    yield _basic_auth_header("dispatcher", "secret")
    settings.dispatcher_basic_username = original_dispatcher_username
    settings.dispatcher_basic_password = original_dispatcher_password


@pytest.fixture()
def viewer_headers():
    original_username = settings.viewer_basic_username
    original_password = settings.viewer_basic_password
    settings.viewer_basic_username = "viewer"
    settings.viewer_basic_password = "secret"
    yield _basic_auth_header("viewer", "secret")
    settings.viewer_basic_username = original_username
    settings.viewer_basic_password = original_password


def test_upload_requires_consent(client, async_session_maker, upload_root, admin_headers):
    booking_id = asyncio.run(_create_booking(async_session_maker, consent=False))
    files = {"file": ("before.jpg", b"abc", "image/jpeg")}

    response = client.post(
        f"/v1/orders/{booking_id}/photos",
        data={"phase": "before"},
        files=files,
        headers=admin_headers,
    )

    assert response.status_code == 403


def test_upload_with_consent_and_download_auth(client, async_session_maker, upload_root, admin_headers):
    booking_id = asyncio.run(_create_booking(async_session_maker, consent=False))

    consent = client.patch(
        f"/v1/orders/{booking_id}/consent_photos",
        json={"consent_photos": True},
        headers=admin_headers,
    )
    assert consent.status_code == 200
    assert consent.json()["consent_photos"] is True

    payload = {"phase": "AFTER"}
    files = {"file": ("after.jpg", b"hello-image", "image/jpeg")}
    upload = client.post(
        f"/v1/orders/{booking_id}/photos", data=payload, files=files, headers=admin_headers
    )
    assert upload.status_code == 201
    photo_id = upload.json()["photo_id"]

    listing = client.get(f"/v1/orders/{booking_id}/photos", headers=admin_headers)
    assert listing.status_code == 200
    assert len(listing.json()["photos"]) == 1

    signed = client.get(
        f"/v1/orders/{booking_id}/photos/{photo_id}/signed_url", headers=admin_headers
    )
    assert signed.status_code == 200
    signed_url = signed.json()["url"]

    download_url = f"/v1/orders/{booking_id}/photos/{photo_id}/download"
    unauthorized = client.get(download_url)
    assert unauthorized.status_code == 401

    download = client.get(download_url, headers=admin_headers)
    assert download.status_code == 200
    assert download.content == b"hello-image"

    signed_fetch = client.get(signed_url)
    assert signed_fetch.status_code == 200
    assert signed_fetch.content == b"hello-image"

    stored_files = list(
        Path(upload_root / "orders" / str(settings.default_org_id) / booking_id).glob("*")
    )
    assert stored_files, "uploaded file should be written to disk"


def test_admin_override_uploads_without_consent(client, async_session_maker, upload_root, admin_headers):
    booking_id = asyncio.run(_create_booking(async_session_maker, consent=False))
    files = {"file": ("before.jpg", b"abc", "image/jpeg")}

    response = client.post(
        f"/v1/orders/{booking_id}/photos",
        data={"phase": "before", "admin_override": "true"},
        files=files,
        headers=admin_headers,
    )

    assert response.status_code == 201


def test_owner_can_admin_override(client, async_session_maker, upload_root, owner_headers):
    """OWNER role has ADMIN permission and can use admin_override."""
    booking_id = asyncio.run(_create_booking(async_session_maker, consent=False))
    files = {"file": ("before.jpg", b"abc", "image/jpeg")}

    response = client.post(
        f"/v1/orders/{booking_id}/photos",
        data={"phase": "before", "admin_override": "true"},
        files=files,
        headers=owner_headers,
    )

    assert response.status_code == 201


def test_dispatcher_cannot_admin_override(client, async_session_maker, upload_root, dispatcher_headers):
    booking_id = asyncio.run(_create_booking(async_session_maker, consent=False))
    files = {"file": ("before.jpg", b"abc", "image/jpeg")}

    response = client.post(
        f"/v1/orders/{booking_id}/photos",
        data={"phase": "before", "admin_override": "true"},
        files=files,
        headers=dispatcher_headers,
    )

    assert response.status_code == 403


def test_viewer_cannot_admin_override(client, async_session_maker, upload_root, viewer_headers):
    booking_id = asyncio.run(_create_booking(async_session_maker, consent=False))
    files = {"file": ("before.jpg", b"abc", "image/jpeg")}

    response = client.post(
        f"/v1/orders/{booking_id}/photos",
        data={"phase": "before", "admin_override": "true"},
        files=files,
        headers=viewer_headers,
    )

    assert response.status_code == 403


def test_staff_can_list_photos_without_consent(client, async_session_maker, upload_root, admin_headers, dispatcher_headers):
    """Staff (admin/dispatcher) can list photos even when consent_photos=false, especially useful for admin_override uploads."""
    booking_id = asyncio.run(_create_booking(async_session_maker, consent=False))

    # Admin uploads photo with admin_override (no consent required)
    files = {"file": ("before.jpg", b"test-photo-data", "image/jpeg")}
    upload_resp = client.post(
        f"/v1/orders/{booking_id}/photos",
        data={"phase": "BEFORE", "admin_override": "true"},
        files=files,
        headers=admin_headers,
    )
    assert upload_resp.status_code == 201
    photo_id = upload_resp.json()["photo_id"]

    # Admin can list photos even though consent_photos=false
    admin_list_resp = client.get(f"/v1/orders/{booking_id}/photos", headers=admin_headers)
    assert admin_list_resp.status_code == 200
    assert len(admin_list_resp.json()["photos"]) == 1
    assert admin_list_resp.json()["photos"][0]["photo_id"] == photo_id

    # Dispatcher can also list photos even though consent_photos=false
    dispatcher_list_resp = client.get(f"/v1/orders/{booking_id}/photos", headers=dispatcher_headers)
    assert dispatcher_list_resp.status_code == 200
    assert len(dispatcher_list_resp.json()["photos"]) == 1
    assert dispatcher_list_resp.json()["photos"][0]["photo_id"] == photo_id


def test_storage_usage_decrements_for_saas(client, async_session_maker, upload_root):
    token, org_id = _create_saas_token(async_session_maker)
    booking_id = asyncio.run(_create_booking(async_session_maker, consent=True))

    files = {"file": ("before.jpg", b"abc", "image/jpeg")}
    upload = client.post(
        f"/v1/orders/{booking_id}/photos",
        data={"phase": "before"},
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert upload.status_code == 201
    photo_id = upload.json()["photo_id"]

    delete = client.delete(
        f"/v1/orders/{booking_id}/photos/{photo_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert delete.status_code == 204

    async def _storage_total() -> int:
        async with async_session_maker() as session:
            total = await session.scalar(
                sa.select(sa.func.coalesce(sa.func.sum(OrganizationUsageEvent.quantity), 0)).where(
                    OrganizationUsageEvent.org_id == org_id,
                    OrganizationUsageEvent.metric == "storage_bytes",
                )
            )
            return int(total or 0)

    assert asyncio.run(_storage_total()) == 0
