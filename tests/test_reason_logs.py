import asyncio
import base64
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.bookings.db_models import Booking
from app.domain.leads.db_models import Lead
from app.domain.reason_logs import schemas as reason_schemas
from app.domain.reason_logs import service as reason_service
from app.domain.time_tracking import service as time_service
from app.settings import settings


def _auth_headers(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.mark.anyio
async def test_time_overrun_requires_reason(async_session_maker):
    original_threshold = settings.time_overrun_reason_threshold
    settings.time_overrun_reason_threshold = 1.1
    try:
        async with async_session_maker() as session:
            start = datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc)
            booking = Booking(
                team_id=1,
                lead_id=None,
                starts_at=start,
                duration_minutes=30,
                planned_minutes=10,
                status="PENDING",
                deposit_required=False,
                deposit_policy=[],
                deposit_status=None,
            )
            session.add(booking)
            await session.commit()
            await session.refresh(booking)

            entry = await time_service.start_time_tracking(
                session, booking.booking_id, worker_id="worker-1", now=start
            )
            finish_at = start + timedelta(minutes=12)

            with pytest.raises(ValueError):
                await time_service.finish_time_tracking(
                    session,
                    booking.booking_id,
                    now=finish_at,
                    reason_provided=False,
                    threshold=settings.time_overrun_reason_threshold,
                )

            await reason_service.create_reason(
                session,
                booking.booking_id,
                kind=reason_schemas.ReasonKind.TIME_OVERRUN,
                code=reason_schemas.ReasonCode.CLIENT_REQUEST,
                note="Client asked for extras",
                created_by="tester",
                time_entry_id=entry.entry_id,
            )
            await session.commit()

            finished = await time_service.finish_time_tracking(
                session,
                booking.booking_id,
                now=finish_at,
                reason_provided=True,
                threshold=settings.time_overrun_reason_threshold,
            )
            assert finished.state == time_service.FINISHED
    finally:
        settings.time_overrun_reason_threshold = original_threshold


def test_create_and_list_reasons(client, async_session_maker):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    async def seed_order() -> str:
        async with async_session_maker() as session:
            booking = Booking(
                team_id=1,
                lead_id=None,
                starts_at=datetime.now(tz=timezone.utc),
                duration_minutes=60,
                planned_minutes=60,
                status="PENDING",
                deposit_required=False,
                deposit_policy=[],
                deposit_status=None,
            )
            session.add(booking)
            await session.commit()
            return booking.booking_id

    order_id = asyncio.run(seed_order())
    auth = (settings.admin_basic_username, settings.admin_basic_password)

    create_resp = client.post(
        f"/v1/orders/{order_id}/reasons",
        auth=auth,
        json={
            "kind": "TIME_OVERRUN",
            "code": "ACCESS_DELAY",
            "note": "Gate locked",
        },
    )
    assert create_resp.status_code == 201
    body = create_resp.json()
    assert body["order_id"] == order_id
    assert body["code"] == "ACCESS_DELAY"

    list_resp = client.get(f"/v1/orders/{order_id}/reasons", auth=auth)
    assert list_resp.status_code == 200
    payload = list_resp.json()
    assert len(payload["reasons"]) == 1
    assert payload["reasons"][0]["note"] == "Gate locked"

    report_resp = client.get("/v1/admin/reasons", auth=auth)
    assert report_resp.status_code == 200
    report_payload = report_resp.json()
    assert report_payload["reasons"]

    csv_resp = client.get(
        "/v1/admin/reasons", auth=auth, params={"format": "csv"}
    )
    assert csv_resp.status_code == 200
    assert "reason_id" in csv_resp.text


def test_invoice_requires_price_adjust_reason(client, async_session_maker):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    async def seed_booking_with_estimate() -> str:
        async with async_session_maker() as session:
            lead = Lead(
                name="Reason Lead",
                phone="780-555-0000",
                email="lead@example.com",
                postal_code="T5A",
                address="1 Test St",
                preferred_dates=["Mon"],
                access_notes=None,
                parking=None,
                pets=None,
                allergies=None,
                notes=None,
                structured_inputs={"beds": 1, "baths": 1, "cleaning_type": "standard"},
                estimate_snapshot={
                    "subtotal_cents": 10000,
                    "pricing_config_version": "v1",
                    "config_hash": "hash",
                },
                pricing_config_version="v1",
                config_hash="hash",
                referral_code=f"REF-{uuid.uuid4().hex[:8]}",
            )
            session.add(lead)
            await session.flush()
            booking = Booking(
                team_id=1,
                lead_id=lead.lead_id,
                starts_at=datetime.now(tz=timezone.utc),
                duration_minutes=60,
                planned_minutes=60,
                status="PENDING",
                deposit_required=False,
                deposit_policy=[],
                deposit_status=None,
            )
            session.add(booking)
            await session.commit()
            return booking.booking_id

    order_id = asyncio.run(seed_booking_with_estimate())
    headers = _auth_headers("admin", "secret")

    invoice_resp = client.post(
        f"/v1/admin/orders/{order_id}/invoice",
        headers=headers,
        json={
            "items": [
                {"description": "Service", "qty": 1, "unit_price_cents": 15000},
            ]
        },
    )
    assert invoice_resp.status_code == 400

    reason_resp = client.post(
        f"/v1/orders/{order_id}/reasons",
        headers=headers,
        json={
            "kind": "PRICE_ADJUST",
            "code": "EXTRA_SERVICE",
            "note": "Added deep clean",
        },
    )
    assert reason_resp.status_code == 201

    invoice_retry = client.post(
        f"/v1/admin/orders/{order_id}/invoice",
        headers=headers,
        json={
            "items": [
                {"description": "Service", "qty": 1, "unit_price_cents": 15000},
            ]
        },
    )
    assert invoice_retry.status_code == 201
