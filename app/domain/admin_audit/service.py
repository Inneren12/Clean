from typing import Any
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_auth import AdminIdentity
from app.domain.admin_audit.db_models import AdminAuditLog
from app.settings import settings


async def record_action(
    session: AsyncSession,
    *,
    identity: AdminIdentity,
    org_id: uuid.UUID | None = None,
    action: str,
    resource_type: str | None,
    resource_id: str | None,
    before: Any,
    after: Any,
) -> AdminAuditLog:
    resolved_org_id = org_id or identity.org_id or settings.default_org_id
    log = AdminAuditLog(
        org_id=resolved_org_id,
        action=action,
        actor=identity.username,
        role=getattr(identity.role, "value", identity.role),
        resource_type=resource_type,
        resource_id=resource_id,
        before=before,
        after=after,
    )
    session.add(log)
    return log
