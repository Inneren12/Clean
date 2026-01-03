import asyncio
import base64
import hashlib
import hmac
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse

import jwt
import pytest
import sqlalchemy as sa

from app.api.photo_tokens import build_photo_download_token
from app.domain.saas import billing_service, service as saas_service
from app.domain.saas.db_models import OrganizationUsageEvent
from app.domain.bookings.db_models import OrderPhoto, OrderPhotoTombstone
from app.infra.storage.backends import LocalStorageBackend
from app.jobs import storage_janitor
from app.infra.storage.backends import S3StorageBackend

from app.domain.bookings.db_models import Booking
from app.domain.leads.db_models import Lead
from app.main import app
from app.settings import settings


def _basic_auth_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


async def _create_booking(async_session_maker, consent: bool = False, org_id=None) -> str:
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
            org_id=org_id,
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


def _create_photo_with_signed_url(client, async_session_maker, upload_root, admin_headers):
    booking_id = asyncio.run(_create_booking(async_session_maker, consent=False))

    consent = client.patch(
        f"/v1/orders/{booking_id}/consent_photos",
        json={"consent_photos": True},
        headers=admin_headers,
    )
    assert consent.status_code == 200

    payload = {"phase": "AFTER"}
    files = {"file": ("after.jpg", b"hello-image", "image/jpeg")}
    upload = client.post(
        f"/v1/orders/{booking_id}/photos", data=payload, files=files, headers=admin_headers
    )
    assert upload.status_code == 201
    photo_id = upload.json()["photo_id"]

    signed = client.get(
        f"/v1/orders/{booking_id}/photos/{photo_id}/signed_url", headers=admin_headers
    )
    assert signed.status_code == 200

    return booking_id, photo_id, signed.json()["url"]


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


class FakeR2Client:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket: str, Key: str, Body: bytes, ContentType: str):
        self.objects[Key] = Body

    def generate_presigned_url(self, ClientMethod: str, Params: dict, ExpiresIn: int) -> str:
        return f"https://r2.example.com/{Params['Key']}?ttl={ExpiresIn}"


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
    booking_id, photo_id, signed_url = _create_photo_with_signed_url(
        client, async_session_maker, upload_root, admin_headers
    )

    listing = client.get(f"/v1/orders/{booking_id}/photos", headers=admin_headers)
    assert listing.status_code == 200
    assert len(listing.json()["photos"]) == 1

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
        Path(upload_root / "org" / str(settings.default_org_id) / "bookings" / booking_id).glob("*")
    )
    assert stored_files, "uploaded file should be written to disk"


def test_signed_download_requires_token(client, async_session_maker, upload_root, admin_headers):
    booking_id, photo_id, _ = _create_photo_with_signed_url(
        client, async_session_maker, upload_root, admin_headers
    )

    unsigned = client.get(f"/v1/orders/{booking_id}/photos/{photo_id}/signed-download")
    assert unsigned.status_code == 401


def test_signed_download_rejects_cross_org_token(
    client, async_session_maker, upload_root, admin_headers
):
    booking_id, photo_id, _ = _create_photo_with_signed_url(
        client, async_session_maker, upload_root, admin_headers
    )

    bad_token = build_photo_download_token(
        org_id=uuid.uuid4(), order_id=booking_id, photo_id=photo_id
    )
    response = client.get(
        f"/v1/orders/{booking_id}/photos/{photo_id}/signed-download?token={bad_token}"
    )

    assert response.status_code == 404


def test_signed_download_rejects_expired_token(client, async_session_maker, upload_root, admin_headers):
    booking_id, photo_id, _ = _create_photo_with_signed_url(
        client, async_session_maker, upload_root, admin_headers
    )

    expired = jwt.encode(
        {
            "org_id": str(settings.default_org_id),
            "order_id": booking_id,
            "photo_id": photo_id,
            "typ": "photo_download",
            "exp": datetime.now(timezone.utc) - timedelta(seconds=60),
        },
        settings.auth_secret_key,
        algorithm="HS256",
    )

    response = client.get(
        f"/v1/orders/{booking_id}/photos/{photo_id}/signed-download?token={expired}"
    )

    assert response.status_code == 401


def test_r2_backend_redirects_with_public_base(
    client, async_session_maker, admin_headers, upload_root
):
    original_backend = settings.order_storage_backend
    original_storage = getattr(app.state, "storage_backend", None)
    settings.order_storage_backend = "r2"
    app.state.storage_backend = S3StorageBackend(
        bucket="test-bucket",
        access_key="ak",
        secret_key="sk",
        region="auto",
        endpoint="https://example.invalid",
        public_base_url="https://cdn.example.com",
        client=FakeR2Client(),
    )

    try:
        booking_id = asyncio.run(_create_booking(async_session_maker, consent=True))
        files = {"file": ("before.jpg", b"abc", "image/jpeg")}
        upload = client.post(
            f"/v1/orders/{booking_id}/photos",
            data={"phase": "before"},
            files=files,
            headers=admin_headers,
        )
        assert upload.status_code == 201
        photo_id = upload.json()["photo_id"]

        async def _fetch_photo() -> OrderPhoto:
            async with async_session_maker() as session:
                return await session.get(OrderPhoto, photo_id)

        photo = asyncio.run(_fetch_photo())
        assert photo is not None
        assert photo.storage_provider == "r2"
        assert photo.storage_key.startswith("org/")

        download = client.get(
            f"/v1/orders/{booking_id}/photos/{photo_id}/download",
            headers=admin_headers,
            allow_redirects=False,
        )

        assert download.status_code in {302, 307}
        assert download.headers["location"].startswith(
            f"https://cdn.example.com/{photo.storage_key}"
        )
    finally:
        settings.order_storage_backend = original_backend
        app.state.storage_backend = original_storage


def test_local_signed_url_validation_roundtrip(tmp_path):
    backend = LocalStorageBackend(root=tmp_path, signing_secret="secret")
    key = "orders/123/photo.jpg"
    base_resource = "http://example.test/orders/123/photos/abc/download"

    signed_url = asyncio.run(
        backend.generate_signed_get_url(key=key, expires_in=60, resource_url=base_resource)
    )

    assert backend.validate_signed_get_url(key=key, url=signed_url)

    parsed = urlparse(signed_url)
    params = dict(parse_qsl(parsed.query))
    params["sig"] = "0" * len(params["sig"])
    tampered_url = parsed._replace(query=urlencode(params)).geturl()

    assert not backend.validate_signed_get_url(key=key, url=tampered_url)

    expired_at = int(time.time()) - 5
    expired_sig = hmac.new(
        backend.signing_secret.encode(), f"{key}:{expired_at}".encode(), hashlib.sha256
    ).hexdigest()
    params["exp"] = str(expired_at)
    params["sig"] = expired_sig
    expired_url = parsed._replace(query=urlencode(params)).geturl()

    assert not backend.validate_signed_get_url(key=key, url=expired_url)


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
    booking_id = asyncio.run(_create_booking(async_session_maker, consent=True, org_id=org_id))

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


def test_delete_failure_creates_tombstone_and_janitor_cleans(client, async_session_maker, upload_root, admin_headers):
    class FailingDeleteStorage(LocalStorageBackend):
        def __init__(self, root: Path):
            super().__init__(root)
            self.fail_once = True

        async def delete(self, *, key: str) -> None:  # type: ignore[override]
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("boom")
            await super().delete(key=key)

    storage = FailingDeleteStorage(upload_root)
    app.state.storage_backend = storage

    booking_id, photo_id, _ = _create_photo_with_signed_url(
        client, async_session_maker, upload_root, admin_headers
    )

    delete_resp = client.delete(
        f"/v1/orders/{booking_id}/photos/{photo_id}",
        headers=admin_headers,
    )
    assert delete_resp.status_code == 204

    async def _fetch_tombstone():
        async with async_session_maker() as session:
            return await session.scalar(sa.select(OrderPhotoTombstone))

    tombstone = asyncio.run(_fetch_tombstone())
    assert tombstone is not None
    assert tombstone.processed_at is None
    assert tombstone.attempts == 1

    async def _run_janitor():
        async with async_session_maker() as session:
            return await storage_janitor.run_storage_janitor(
                session, storage, retry_interval_seconds=0
            )

    result = asyncio.run(_run_janitor())
    assert result["processed"] == 1

    tombstone_after = asyncio.run(_fetch_tombstone())
    assert tombstone_after is not None
    assert tombstone_after.processed_at is not None
    assert tombstone_after.last_error is None
    app.state.storage_backend = None
