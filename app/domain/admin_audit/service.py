from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_auth import _explicit_admin_audit

from app.api.admin_auth import AdminIdentity
from app.domain.admin_audit.db_models import AdminAuditLog


async def record_action(
    session: AsyncSession,
    *,
    identity: AdminIdentity,
    action: str,
    resource_type: str | None,
    resource_id: str | None,
    before: Any,
    after: Any,
) -> AdminAuditLog:
    log = AdminAuditLog(
        action=action,
        actor=identity.username,
        role=identity.role.value,
        resource_type=resource_type,
        resource_id=resource_id,
        before=before,
        after=after,
    )
    session.add(log)
    _explicit_admin_audit.set(True)
    return log
