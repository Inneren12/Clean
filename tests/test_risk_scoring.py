import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.domain.bookings import service as booking_service
from app.domain.bookings.db_models import Booking
from app.domain.leads.db_models import Lead
from app.settings import settings


async def _create_lead(session, *, postal_code: str, estimate_total: float) -> Lead:
    lead = Lead(
        name="Risk Test",
        phone="780-555-1234",
        email=f"risk-{uuid4()}@example.com",
        postal_code=postal_code,
        preferred_dates=[],
        structured_inputs={"beds": 2, "baths": 1, "cleaning_type": "standard"},
        estimate_snapshot={
            "pricing_config_version": "v1",
            "config_hash": "hash",
            "total_before_tax": estimate_total,
        },
        pricing_config_version="v1",
        config_hash="hash",
        status="NEW",
    )
    session.add(lead)
    await session.commit()
    await session.refresh(lead)
    return lead


@pytest.mark.parametrize(
    "postal_code, hours_ahead, estimate_total, cancellations, expected_band, expected_reasons",
    [
        ("T1A0A0", 72, 150.0, 0, booking_service.RiskBand.LOW, {"new_client"}),
        ("T1A0A0", 48, 400.0, 0, booking_service.RiskBand.MEDIUM, {"new_client", "high_total"}),
        (
            "T9X1A1",
            30,
            400.0,
            0,
            booking_service.RiskBand.MEDIUM,
            {"new_client", "high_total", "area_flagged"},
        ),
        ("T1A0A0", 6, 180.0, 2, booking_service.RiskBand.HIGH, {"short_notice", "cancel_history"}),
    ],
)
def test_risk_assessment_table(
    async_session_maker,
    postal_code: str,
    hours_ahead: int,
    estimate_total: float,
    cancellations: int,
    expected_band: booking_service.RiskBand,
    expected_reasons: set[str],
) -> None:
    starts_at = datetime.now(tz=timezone.utc) + timedelta(hours=hours_ahead)

    async def _run() -> None:
        async with async_session_maker() as session:
            lead = await _create_lead(session, postal_code=postal_code, estimate_total=estimate_total)

            for idx in range(cancellations):
                session.add(
                    Booking(
                        booking_id=f"cancel-{idx}-{uuid4()}",
                        team_id=1,
                        lead_id=lead.lead_id,
                        starts_at=datetime.now(tz=timezone.utc) - timedelta(days=idx + 1),
                        duration_minutes=60,
                        status="CANCELLED",
                    )
                )
            if cancellations:
                await session.commit()

            assessment = await booking_service.evaluate_risk(
                session=session,
                lead=lead,
                client_id=None,
                starts_at=starts_at,
                postal_code=postal_code,
            )

            assert assessment.band == expected_band
            assert set(assessment.reasons) >= expected_reasons
            assert assessment.score >= 0

    asyncio.run(_run())


def test_high_risk_requires_manual_confirmation(async_session_maker) -> None:
    settings.deposits_enabled = True
    start_local = (
        datetime.now(tz=booking_service.LOCAL_TZ)
        .replace(hour=10, minute=0, second=0, microsecond=0)
        + timedelta(days=1)
    )
    starts_at = start_local.astimezone(timezone.utc)

    async def _run() -> None:
        async with async_session_maker() as session:
            lead = await _create_lead(session, postal_code="T1A0A0", estimate_total=500.0)
            session.add(
                Booking(
                    booking_id="cancel-seed",
                    team_id=1,
                    lead_id=lead.lead_id,
                    starts_at=datetime.now(tz=timezone.utc) - timedelta(days=3),
                    duration_minutes=60,
                    status="CANCELLED",
                )
            )
            await session.commit()

            risk = await booking_service.evaluate_risk(
                session=session,
                lead=lead,
                client_id=None,
                starts_at=starts_at,
                postal_code=lead.postal_code,
            )
            assert risk.band == booking_service.RiskBand.HIGH

            deposit_decision = await booking_service.evaluate_deposit_policy(
                session=session,
                lead=lead,
                starts_at=starts_at,
                deposit_percent=0.25,
                deposits_enabled=True,
                service_type=None,
                estimated_total=lead.estimate_snapshot.get("total_before_tax"),
                force_deposit=risk.requires_deposit,
                extra_reasons=[f"risk_{risk.band.value.lower()}"] if risk.requires_deposit else None,
            )

            booking = await booking_service.create_booking(
                starts_at=starts_at,
                duration_minutes=90,
                lead_id=lead.lead_id,
                session=session,
                deposit_decision=deposit_decision,
                policy_snapshot=deposit_decision.policy_snapshot,
                risk_assessment=risk,
            )
            await booking_service.attach_checkout_session(
                session,
                booking.booking_id,
                checkout_session_id="cs_manual",
                payment_intent_id="pi_manual",
                commit=True,
            )

            updated = await booking_service.mark_deposit_paid(
                session=session,
                checkout_session_id=None,
                payment_intent_id="pi_manual",
                email_adapter=None,
            )
            assert updated is not None
            assert updated.deposit_status == "paid"
            assert updated.status == "PENDING"

    asyncio.run(_run())
