from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.leads.db_models import ChatSession, Lead
from app.domain.leads.statuses import LEAD_STATUS_BOOKED, LEAD_STATUS_DONE
from app.settings import settings


async def cleanup_retention(session: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    reference_time = now or datetime.now(timezone.utc)
    chat_cutoff = reference_time - timedelta(days=settings.retention_chat_days)
    leads_cutoff = reference_time - timedelta(days=settings.retention_lead_days)

    chat_result = await session.execute(delete(ChatSession).where(ChatSession.updated_at < chat_cutoff))
    chat_deleted = chat_result.rowcount or 0

    leads_deleted = 0
    if settings.retention_enable_leads:
        leads_result = await session.execute(
            delete(Lead).where(
                Lead.created_at < leads_cutoff,
                ~Lead.status.in_([LEAD_STATUS_BOOKED, LEAD_STATUS_DONE]),
            )
        )
        leads_deleted = leads_result.rowcount or 0

    await session.commit()
    return {"chat_sessions_deleted": chat_deleted, "leads_deleted": leads_deleted}
