import asyncio
import base64
import uuid
from datetime import datetime, timezone

import hashlib
import hmac
from urllib.parse import parse_qsl, urlparse

import httpx
import pytest
from sqlalchemy import select

from app.domain.bookings.db_models import Booking, OrderPhoto
from app.domain.leads.db_models import Lead
from app.domain.saas.service import ensure_org
from app.infra.storage.backends import CloudflareImagesStorageBackend
from app.main import app
from app.settings import settings


def _auth_headers(username: str, password: str, org_id: uuid.UUID) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}", "X-Test-Org": str(org_id)}


def _configure_cf_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "order_storage_backend", "cloudflare_images")
    monkeypatch.setattr(settings, "cf_images_account_id", "acct-123")
    monkeypatch.setattr(settings, "cf_images_account_hash", "hash-abc")
    monkeypatch.setattr(settings, "cf_images_api_token", "secret-token")
    monkeypatch.setattr(settings, "cf_images_default_variant", "public")
    monkeypatch.setattr(settings, "cf_images_thumbnail_variant", "thumbnail")
    monkeypatch.setattr(settings, "cf_images_signing_key", "cf-signing-key")
    monkeypatch.setattr(settings, "auth_secret_key", "cf-photo-secret")
    monkeypatch.setattr(settings, "admin_basic_username", "admin")
    monkeypatch.setattr(settings, "admin_basic_password", "secret")


def _seed_booking(async_session_maker, org_id: uuid.UUID) -> str:
    async def _seed() -> str:
        async with async_session_maker() as session:
            await ensure_org(session, org_id, name="Cloudflare Org")
            lead = Lead(
                org_id=org_id,
                name="CF Lead",
                phone="780-000-0000",
                email="cf@example.com",
                postal_code="T5A",
                address="1 CF St",
                preferred_dates=["Mon"],
                structured_inputs={},
                estimate_snapshot={"price_cents": 1000, "line_items": []},
                pricing_config_version="v1",
                config_hash="hash",
            )
            booking = Booking(
                org_id=org_id,
                team_id=1,
                lead_id=lead.lead_id,
                starts_at=datetime.now(tz=timezone.utc),
                duration_minutes=60,
                status="CONFIRMED",
                consent_photos=True,
            )
            session.add_all([lead, booking])
            await session.commit()
            await session.refresh(booking)
            return booking.booking_id

    return asyncio.run(_seed())


def _mock_backend(transport: httpx.MockTransport) -> CloudflareImagesStorageBackend:
    client = httpx.AsyncClient(transport=transport, base_url="https://api.cloudflare.com")
    backend = CloudflareImagesStorageBackend(
        account_id=settings.cf_images_account_id or "acct-123",
        api_token=settings.cf_images_api_token or "token",
        account_hash=settings.cf_images_account_hash or "hash-abc",
        default_variant=settings.cf_images_default_variant,
        signing_key=settings.cf_images_signing_key,
        max_payload_bytes=settings.order_photo_max_bytes,
        client=client,
    )
    return backend


def test_cloudflare_images_upload_and_redirect(
    client, async_session_maker, monkeypatch
):
    _configure_cf_settings(monkeypatch)
    org_id = uuid.uuid4()
    headers = _auth_headers("admin", "secret", org_id)

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"success": True, "result": {"id": "img-123"}}
        )
    )
    backend = _mock_backend(transport)
    app.state.storage_backend = backend
    app.state.storage_backend_config = None

    booking_id = _seed_booking(async_session_maker, org_id)

    files = {"file": ("after.jpg", b"hello-image", "image/jpeg")}
    response = client.post(
        f"/v1/orders/{booking_id}/photos",
        data={"phase": "AFTER"},
        files=files,
        headers=headers,
    )

    assert response.status_code == 201, response.text
    photo_id = response.json()["photo_id"]

    async def _load_photo() -> OrderPhoto:
        async with async_session_maker() as session:
            result = await session.execute(select(OrderPhoto).where(OrderPhoto.photo_id == photo_id))
            return result.scalar_one()

    photo = asyncio.run(_load_photo())
    assert photo.storage_provider == "cloudflare_images"
    assert photo.storage_key == "img-123"

    download = client.get(
        f"/v1/orders/{booking_id}/photos/{photo_id}/download", headers=headers, allow_redirects=False
    )
    assert download.status_code == 307, download.text
    location = download.headers["location"]
    parsed = urlparse(location)
    assert parsed.path == "/hash-abc/img-123/public"
    params = dict(parse_qsl(parsed.query))
    assert "exp" in params and "sig" in params
    expected_sig = hmac.new(
        settings.cf_images_signing_key.encode(),
        f"{parsed.path}?exp={params['exp']}".encode(),
        hashlib.sha256,
    ).hexdigest()
    assert params["sig"] == expected_sig

    asyncio.run(backend.client.aclose())


def test_cloudflare_images_upload_failure_maps_to_gateway_error(client, async_session_maker, monkeypatch):
    _configure_cf_settings(monkeypatch)
    org_id = uuid.uuid4()
    headers = _auth_headers("admin", "secret", org_id)

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(500, json={"success": False, "errors": ["boom"]})
        return httpx.Response(500, json={"success": False})

    backend = _mock_backend(httpx.MockTransport(_handler))
    app.state.storage_backend = backend
    app.state.storage_backend_config = None

    booking_id = _seed_booking(async_session_maker, org_id)

    files = {"file": ("before.png", b"abc", "image/png")}
    response = client.post(
        f"/v1/orders/{booking_id}/photos",
        data={"phase": "BEFORE"},
        files=files,
        headers=headers,
    )

    assert response.status_code in {502, 503}
    asyncio.run(backend.client.aclose())
