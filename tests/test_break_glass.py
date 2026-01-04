from datetime import datetime, timedelta, timezone

from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from app.api.break_glass import BREAK_GLASS_HEADER
from app.domain.admin_audit.db_models import AdminAuditLog
from app.domain.break_glass.db_models import BreakGlassSession
from app.settings import settings


@pytest.mark.anyio
async def test_break_glass_start_requires_reason_and_audits(async_session_maker, client):
    settings.admin_basic_username = "owner"
    settings.admin_basic_password = "secret"

    response = client.post(
        "/v1/admin/break-glass/start",
        json={"reason": "incident start", "ttl_minutes": 15},
        auth=("owner", "secret"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["token"]
    assert payload["expires_at"]

    async with async_session_maker() as session:
        result = await session.execute(
            sa.select(AdminAuditLog)
            .where(AdminAuditLog.action == "break_glass_start")
            .order_by(AdminAuditLog.created_at.desc())
        )
        log = result.scalars().first()
        assert log is not None
        assert log.after.get("reason") == "incident start"


@pytest.mark.anyio
async def test_break_glass_token_expires(async_session_maker, client):
    settings.admin_basic_username = "owner"
    settings.admin_basic_password = "secret"
    settings.admin_read_only = True

    start = client.post(
        "/v1/admin/break-glass/start",
        json={"reason": "expire", "ttl_minutes": 1},
        auth=("owner", "secret"),
    )
    token = start.json()["token"]

    async with async_session_maker() as session:
        await session.execute(
            sa.update(BreakGlassSession).values(
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)
            )
        )
        await session.commit()

    response = client.post(
        "/v1/admin/pricing/reload",
        auth=("owner", "secret"),
        headers={BREAK_GLASS_HEADER: token},
    )

    assert response.status_code == 409


@pytest.mark.anyio
async def test_read_only_allows_break_glass_write(async_session_maker, client):
    settings.admin_basic_username = "owner"
    settings.admin_basic_password = "secret"
    settings.admin_read_only = True

    start = client.post(
        "/v1/admin/break-glass/start",
        json={"reason": "allow write"},
        auth=("owner", "secret"),
    )
    token = start.json()["token"]

    denied = client.post("/v1/admin/pricing/reload", auth=("owner", "secret"))
    assert denied.status_code == 409

    allowed = client.post(
        "/v1/admin/pricing/reload",
        auth=("owner", "secret"),
        headers={BREAK_GLASS_HEADER: token},
    )
    assert allowed.status_code == 202

    async with async_session_maker() as session:
        result = await session.execute(
            sa.select(AdminAuditLog)
            .where(AdminAuditLog.action == "break_glass_write")
            .order_by(AdminAuditLog.created_at.desc())
        )
        log = result.scalars().first()
        assert log is not None
        assert log.after.get("reason") == "allow write"
