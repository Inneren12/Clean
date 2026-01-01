"""Cross-org isolation tests for leads domain."""

import pytest
import uuid
from unittest.mock import patch

from app.domain.leads.db_models import Lead
from app.domain.leads.statuses import LEAD_STATUS_NEW, LEAD_STATUS_CONTACTED


@pytest.fixture
def mock_org_resolver():
    """Fixture that provides a mock for resolve_org_id to read from X-Test-Org header."""
    def _resolve_from_header(request):
        test_org = request.headers.get("X-Test-Org")
        if test_org:
            return uuid.UUID(test_org)
        from app.settings import settings
        return settings.default_org_id

    with patch("app.api.entitlements.resolve_org_id", side_effect=_resolve_from_header):
        yield


@pytest.mark.anyio
async def test_leads_list_isolation(async_session_maker, client, mock_org_resolver):
    """Test that list endpoint only returns leads from the user's org."""
    org_a_id = uuid.uuid4()
    org_b_id = uuid.uuid4()

    # Create leads for org A and org B
    async with async_session_maker() as session:
        lead_a = Lead(
            org_id=org_a_id,
            name="Alice from Org A",
            phone="111-1111",
            email="alice@orga.example.com",
            structured_inputs={},
            estimate_snapshot={},
            pricing_config_version="v1",
            config_hash="hash1",
            status=LEAD_STATUS_NEW,
        )
        lead_b = Lead(
            org_id=org_b_id,
            name="Bob from Org B",
            phone="222-2222",
            email="bob@orgb.example.com",
            structured_inputs={},
            estimate_snapshot={},
            pricing_config_version="v1",
            config_hash="hash2",
            status=LEAD_STATUS_NEW,
        )
        session.add(lead_a)
        session.add(lead_b)
        await session.commit()

    # Query as org A - should only see org A leads
    response = client.get(
        "/v1/admin/leads",
        headers={"X-Test-Org": str(org_a_id)},
    )

    assert response.status_code == 200
    leads = response.json()
    assert len(leads) == 1
    assert leads[0]["name"] == "Alice from Org A"

    # Query as org B - should only see org B leads
    response = client.get(
        "/v1/admin/leads",
        headers={"X-Test-Org": str(org_b_id)},
    )

    assert response.status_code == 200
    leads = response.json()
    assert len(leads) == 1
    assert leads[0]["name"] == "Bob from Org B"


@pytest.mark.anyio
async def test_leads_detail_isolation(async_session_maker, client, mock_org_resolver):
    """Test that org A cannot access org B's lead by ID."""
    org_a_id = uuid.uuid4()
    org_b_id = uuid.uuid4()

    # Create a lead for org B
    lead_b_id = None
    async with async_session_maker() as session:
        lead_b = Lead(
            org_id=org_b_id,
            name="Bob from Org B",
            phone="222-2222",
            email="bob@orgb.example.com",
            structured_inputs={},
            estimate_snapshot={},
            pricing_config_version="v1",
            config_hash="hash2",
            status=LEAD_STATUS_NEW,
        )
        session.add(lead_b)
        await session.commit()
        await session.refresh(lead_b)
        lead_b_id = lead_b.lead_id

    # Try to update org B's lead as org A - should get 404
    response = client.post(
        f"/v1/admin/leads/{lead_b_id}/status",
        headers={"X-Test-Org": str(org_a_id)},
        json={"status": "CONTACTED"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Lead not found"


@pytest.mark.anyio
async def test_leads_update_isolation(async_session_maker, client, mock_org_resolver):
    """Test that update is scoped by org_id."""
    org_a_id = uuid.uuid4()
    org_b_id = uuid.uuid4()

    # Create leads for both orgs
    lead_a_id = None
    lead_b_id = None
    async with async_session_maker() as session:
        lead_a = Lead(
            org_id=org_a_id,
            name="Alice from Org A",
            phone="111-1111",
            email="alice@orga.example.com",
            structured_inputs={},
            estimate_snapshot={},
            pricing_config_version="v1",
            config_hash="hash1",
            status=LEAD_STATUS_NEW,
        )
        lead_b = Lead(
            org_id=org_b_id,
            name="Bob from Org B",
            phone="222-2222",
            email="bob@orgb.example.com",
            structured_inputs={},
            estimate_snapshot={},
            pricing_config_version="v1",
            config_hash="hash2",
            status=LEAD_STATUS_NEW,
        )
        session.add(lead_a)
        session.add(lead_b)
        await session.commit()
        await session.refresh(lead_a)
        await session.refresh(lead_b)
        lead_a_id = lead_a.lead_id
        lead_b_id = lead_b.lead_id

    # Update org A's lead as org A - should succeed
    response = client.post(
        f"/v1/admin/leads/{lead_a_id}/status",
        headers={"X-Test-Org": str(org_a_id)},
        json={"status": "CONTACTED"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "CONTACTED"

    # Try to update org B's lead as org A - should get 404
    response = client.post(
        f"/v1/admin/leads/{lead_b_id}/status",
        headers={"X-Test-Org": str(org_a_id)},
        json={"status": "CONTACTED"},
    )

    assert response.status_code == 404

    # Verify org B's lead was not modified
    async with async_session_maker() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(Lead).where(Lead.lead_id == lead_b_id)
        )
        lead_b = result.scalar_one()
        assert lead_b.status == LEAD_STATUS_NEW


@pytest.mark.anyio
async def test_leads_referral_isolation(async_session_maker, client, mock_org_resolver):
    """Test that referral codes only work within the same org."""
    org_a_id = uuid.uuid4()
    org_b_id = uuid.uuid4()

    # Create a lead in org A with a referral code
    referral_code_a = None
    async with async_session_maker() as session:
        lead_a = Lead(
            org_id=org_a_id,
            name="Alice from Org A",
            phone="111-1111",
            email="alice@orga.example.com",
            structured_inputs={},
            estimate_snapshot={},
            pricing_config_version="v1",
            config_hash="hash1",
            status=LEAD_STATUS_NEW,
        )
        session.add(lead_a)
        await session.commit()
        await session.refresh(lead_a)
        referral_code_a = lead_a.referral_code

    # Try to create a lead in org B using org A's referral code - should fail
    # Note: create_lead is a public endpoint, but it should still respect org scoping
    # For now we'll test with the mock org resolver
    async def mock_verify_turnstile(*args, **kwargs):
        return True

    with patch("app.infra.captcha.verify_turnstile", side_effect=mock_verify_turnstile):
        response = client.post(
            "/v1/leads",
            headers={"X-Test-Org": str(org_b_id)},
            json={
                "name": "Bob from Org B",
                "phone": "222-2222",
                "email": "bob@orgb.example.com",
                "structured_inputs": {
                    "service_type": "deep",
                    "bedrooms": 2,
                    "bathrooms": 1,
                    "frequency": "one_time",
                },
                "estimate_snapshot": {
                    "subtotal_cents": 15000,
                    "tax_cents": 1200,
                    "total_cents": 16200,
                    "pricing_config_version": "v1",
                    "config_hash": "hash2",
                },
                "captcha_token": "mock-token",
                "referral_code": referral_code_a,  # Trying to use org A's code
            },
        )

    # Should fail because referral code is from a different org
    assert response.status_code == 400
    assert "Invalid referral code" in response.json()["detail"]


@pytest.mark.anyio
async def test_leads_search_by_status_isolation(async_session_maker, client, mock_org_resolver):
    """Test that status filter respects org scoping."""
    org_a_id = uuid.uuid4()
    org_b_id = uuid.uuid4()

    # Create leads with different statuses in both orgs
    async with async_session_maker() as session:
        lead_a1 = Lead(
            org_id=org_a_id,
            name="Alice 1 from Org A",
            phone="111-1111",
            email="alice1@orga.example.com",
            structured_inputs={},
            estimate_snapshot={},
            pricing_config_version="v1",
            config_hash="hash1",
            status=LEAD_STATUS_NEW,
        )
        lead_a2 = Lead(
            org_id=org_a_id,
            name="Alice 2 from Org A",
            phone="111-2222",
            email="alice2@orga.example.com",
            structured_inputs={},
            estimate_snapshot={},
            pricing_config_version="v1",
            config_hash="hash1",
            status=LEAD_STATUS_CONTACTED,
        )
        lead_b = Lead(
            org_id=org_b_id,
            name="Bob from Org B",
            phone="222-2222",
            email="bob@orgb.example.com",
            structured_inputs={},
            estimate_snapshot={},
            pricing_config_version="v1",
            config_hash="hash2",
            status=LEAD_STATUS_NEW,
        )
        session.add_all([lead_a1, lead_a2, lead_b])
        await session.commit()

    # Query org A for NEW leads - should only see org A's NEW lead
    response = client.get(
        "/v1/admin/leads?status=NEW",
        headers={"X-Test-Org": str(org_a_id)},
    )

    assert response.status_code == 200
    leads = response.json()
    assert len(leads) == 1
    assert leads[0]["name"] == "Alice 1 from Org A"
    assert leads[0]["status"] == "NEW"

    # Query org A for CONTACTED leads - should only see org A's CONTACTED lead
    response = client.get(
        "/v1/admin/leads?status=CONTACTED",
        headers={"X-Test-Org": str(org_a_id)},
    )

    assert response.status_code == 200
    leads = response.json()
    assert len(leads) == 1
    assert leads[0]["name"] == "Alice 2 from Org A"
    assert leads[0]["status"] == "CONTACTED"
