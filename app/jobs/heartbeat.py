from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.ops.db_models import JobHeartbeat


async def record_heartbeat(session_factory: async_sessionmaker, name: str = "jobs-runner") -> None:
    now = datetime.now(tz=timezone.utc)
    async with session_factory() as session:
        heartbeat = await session.get(JobHeartbeat, name)
        if heartbeat is None:
            heartbeat = JobHeartbeat(name=name, last_heartbeat=now, updated_at=now)
            session.add(heartbeat)
        else:
            heartbeat.last_heartbeat = now
        await session.commit()
