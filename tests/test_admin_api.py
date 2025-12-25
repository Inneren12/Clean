import base64

from app.settings import settings


def _basic_auth_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_admin_leads_requires_auth(client):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    response = client.get("/v1/admin/leads")
    assert response.status_code == 401

    auth_headers = _basic_auth_header("admin", "secret")
    authorized = client.get("/v1/admin/leads", headers=auth_headers)
    assert authorized.status_code == 200
    assert isinstance(authorized.json(), list)


def test_admin_cleanup_removes_old_pending_bookings(client, async_session_maker):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    async def _seed() -> None:
        from datetime import datetime, timedelta, timezone

        from app.domain.bookings.db_models import Booking

        async with async_session_maker() as session:
            old_booking = Booking(
                team_id=1,
                starts_at=datetime.now(tz=timezone.utc) - timedelta(days=1),
                duration_minutes=60,
                status="PENDING",
                created_at=datetime.now(tz=timezone.utc) - timedelta(hours=2),
            )
            fresh_booking = Booking(
                team_id=1,
                starts_at=datetime.now(tz=timezone.utc) + timedelta(hours=2),
                duration_minutes=60,
                status="PENDING",
                created_at=datetime.now(tz=timezone.utc),
            )
            session.add_all([old_booking, fresh_booking])
            await session.commit()

    import asyncio

    asyncio.run(_seed())

    headers = _basic_auth_header("admin", "secret")
    response = client.post("/v1/admin/cleanup", headers=headers)
    assert response.status_code == 202
    assert response.json()["deleted"] == 1
