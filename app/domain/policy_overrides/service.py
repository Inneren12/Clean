from __future__ import annotations

import copy
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.policy_overrides.db_models import PolicyOverrideAudit
from app.domain.policy_overrides.schemas import OverrideType


async def record_override(
    session: AsyncSession,
    *,
    booking_id: str,
    override_type: OverrideType,
    actor: str,
    reason: str,
    old_value: dict[str, Any],
    new_value: dict[str, Any],
) -> PolicyOverrideAudit:
    audit = PolicyOverrideAudit(
        booking_id=booking_id,
        override_type=override_type.value,
        actor=actor,
        reason=reason,
        old_value=copy.deepcopy(old_value),
        new_value=copy.deepcopy(new_value),
    )
    session.add(audit)
    await session.flush()
    return audit


async def list_overrides(
    session: AsyncSession,
    *,
    booking_id: str | None = None,
    override_type: OverrideType | None = None,
) -> list[PolicyOverrideAudit]:
    stmt: Select[tuple[PolicyOverrideAudit]] = select(PolicyOverrideAudit)
    filters: list[object] = []
    if booking_id:
        filters.append(PolicyOverrideAudit.booking_id == booking_id)
    if override_type:
        filters.append(PolicyOverrideAudit.override_type == override_type.value)
    if filters:
        stmt = stmt.where(*filters)
    stmt = stmt.order_by(PolicyOverrideAudit.created_at.desc())
    result = await session.execute(stmt)
    return result.scalars().all()
