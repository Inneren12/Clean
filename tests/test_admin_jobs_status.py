import anyio
from datetime import datetime, timezone

from app.domain.ops.db_models import JobHeartbeat
from app.settings import settings


def test_admin_jobs_status_lists_failures(client, async_session_maker):
    settings.viewer_basic_username = "viewer"
    settings.viewer_basic_password = "password"
    now = datetime.now(tz=timezone.utc)

    async def seed_jobs():
        async with async_session_maker() as session:
            session.add_all(
                [
                    JobHeartbeat(
                        name="jobs-runner",
                        last_heartbeat=now,
                        last_success_at=now,
                        consecutive_failures=0,
                    ),
                    JobHeartbeat(
                        name="email-dlq",
                        last_heartbeat=now,
                        last_success_at=now,
                        last_error="timeout",
                        last_error_at=now,
                        consecutive_failures=2,
                    ),
                ]
            )
            await session.commit()

    anyio.run(seed_jobs)

    response = client.get("/v1/admin/jobs/status", auth=("viewer", "password"))
    assert response.status_code == 200
    payload = response.json()
    assert any(entry["name"] == "jobs-runner" for entry in payload)
    dlq_entry = next(entry for entry in payload if entry["name"] == "email-dlq")
    assert dlq_entry["consecutive_failures"] == 2
    assert dlq_entry["last_error"] == "timeout"
    assert dlq_entry["last_error_at"]
