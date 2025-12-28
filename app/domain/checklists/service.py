import logging
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.bookings.db_models import Booking
from app.domain.checklists import schemas as checklist_schemas
from app.domain.checklists.db_models import (
    ChecklistRun,
    ChecklistRunItem,
    ChecklistTemplate,
    ChecklistTemplateItem,
)

logger = logging.getLogger(__name__)

STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _positioned(items: Iterable[checklist_schemas.ChecklistTemplateItemPayload]) -> list[dict]:
    positioned: list[dict] = []
    for idx, item in enumerate(items):
        position = item.position if item.position is not None else idx
        positioned.append({
            "position": position,
            "label": item.label,
            "phase": item.phase,
            "required": item.required,
        })
    positioned.sort(key=lambda itm: itm["position"])
    for idx, itm in enumerate(positioned):
        itm["position"] = idx
    return positioned


async def list_templates(session: AsyncSession) -> list[ChecklistTemplate]:
    stmt: Select[ChecklistTemplate] = (
        select(ChecklistTemplate)
        .options(selectinload(ChecklistTemplate.items))
        .order_by(ChecklistTemplate.service_type, ChecklistTemplate.version.desc())
    )
    result = await session.execute(stmt)
    return result.scalars().unique().all()


async def _next_version(session: AsyncSession, service_type: str | None) -> int:
    stmt = select(func.max(ChecklistTemplate.version)).where(
        ChecklistTemplate.service_type.is_(service_type)
        if service_type is None
        else ChecklistTemplate.service_type == service_type
    )
    result = await session.execute(stmt)
    max_version = result.scalar_one_or_none() or 0
    return max_version + 1


async def create_template(
    session: AsyncSession, request: checklist_schemas.ChecklistTemplateRequest
) -> ChecklistTemplate:
    version = request.version or await _next_version(session, request.service_type)
    template = ChecklistTemplate(
        name=request.name,
        service_type=request.service_type,
        version=version,
        is_active=request.is_active,
    )
    for item in _positioned(request.items):
        template.items.append(ChecklistTemplateItem(**item))
    session.add(template)
    await session.commit()
    await session.refresh(template)
    result = await session.execute(
        select(ChecklistTemplate)
        .options(selectinload(ChecklistTemplate.items))
        .where(ChecklistTemplate.template_id == template.template_id)
    )
    template = result.scalar_one()
    logger.info(
        "checklist_template_created",
        extra={"extra": {"template_id": template.template_id, "service_type": template.service_type}},
    )
    return template


async def update_template(
    session: AsyncSession, template_id: int, request: checklist_schemas.ChecklistTemplateUpdateRequest
) -> ChecklistTemplate | None:
    stmt = (
        select(ChecklistTemplate)
        .options(selectinload(ChecklistTemplate.items))
        .where(ChecklistTemplate.template_id == template_id)
    )
    result = await session.execute(stmt)
    template = result.scalar_one_or_none()
    if template is None:
        return None

    if request.name is not None:
        template.name = request.name
    if request.service_type is not None:
        template.service_type = request.service_type
    if request.version is not None:
        template.version = request.version
    if request.is_active is not None:
        template.is_active = request.is_active
    if request.items is not None:
        template.items.clear()
        for item in _positioned(request.items):
            template.items.append(ChecklistTemplateItem(**item))

    await session.commit()
    await session.refresh(template)
    await session.refresh(template, attribute_names=["items"])
    logger.info(
        "checklist_template_updated",
        extra={"extra": {"template_id": template.template_id, "service_type": template.service_type}},
    )
    return template


async def delete_template(session: AsyncSession, template_id: int) -> bool:
    template = await session.get(ChecklistTemplate, template_id)
    if template is None:
        return False
    await session.delete(template)
    await session.commit()
    logger.info(
        "checklist_template_deleted",
        extra={"extra": {"template_id": template_id, "service_type": template.service_type}},
    )
    return True


async def _latest_active_template(
    session: AsyncSession, service_type: str | None
) -> ChecklistTemplate | None:
    stmt = (
        select(ChecklistTemplate)
        .options(selectinload(ChecklistTemplate.items))
        .where(ChecklistTemplate.is_active.is_(True))
    )
    if service_type is None:
        stmt = stmt.where(ChecklistTemplate.service_type.is_(None))
    else:
        stmt = stmt.where(ChecklistTemplate.service_type == service_type)
    stmt = stmt.order_by(ChecklistTemplate.version.desc()).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def resolve_template(
    session: AsyncSession, template_id: int | None, service_type: str | None
) -> ChecklistTemplate | None:
    if template_id is not None:
        stmt = (
            select(ChecklistTemplate)
            .options(selectinload(ChecklistTemplate.items))
            .where(ChecklistTemplate.template_id == template_id)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    template = await _latest_active_template(session, service_type)
    if template:
        return template
    if service_type is not None:
        return await _latest_active_template(session, None)
    return None


async def find_run_by_order(session: AsyncSession, order_id: str) -> ChecklistRun | None:
    stmt = (
        select(ChecklistRun)
        .options(
            selectinload(ChecklistRun.items).selectinload(ChecklistRunItem.template_item),
            selectinload(ChecklistRun.template),
        )
        .where(ChecklistRun.order_id == order_id)
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def init_checklist(
    session: AsyncSession, order_id: str, template_id: int | None, service_type: str | None
) -> ChecklistRun | None:
    existing = await find_run_by_order(session, order_id)
    if existing:
        return existing

    booking = await session.get(Booking, order_id)
    if booking is None:
        return None

    template = await resolve_template(session, template_id, service_type)
    if template is None:
        raise ValueError("No checklist template available for order")

    run = ChecklistRun(
        order_id=order_id,
        template_id=template.template_id,
        status=STATUS_IN_PROGRESS,
    )
    for item in template.items:
        run.items.append(
            ChecklistRunItem(
                template_item_id=item.item_id,
                checked=False,
                note=None,
            )
        )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    await session.refresh(run, attribute_names=["items"])
    logger.info(
        "checklist_init",
        extra={"extra": {"order_id": order_id, "run_id": run.run_id, "template_id": template.template_id}},
    )
    return await find_run_by_order(session, order_id)


async def _get_run_item(
    session: AsyncSession, order_id: str, run_item_id: str
) -> ChecklistRunItem | None:
    stmt = (
        select(ChecklistRunItem)
        .join(ChecklistRun)
        .options(
            selectinload(ChecklistRunItem.template_item),
            selectinload(ChecklistRunItem.run),
        )
        .where(ChecklistRun.order_id == order_id, ChecklistRunItem.run_item_id == run_item_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def toggle_item(
    session: AsyncSession, order_id: str, run_item_id: str, patch: checklist_schemas.ChecklistRunItemPatch
) -> ChecklistRunItem | None:
    item = await _get_run_item(session, order_id, run_item_id)
    if item is None:
        return None
    if item.run.status == STATUS_COMPLETED:
        raise ValueError("Checklist already completed")

    if patch.checked is not None:
        item.checked = patch.checked
        item.checked_at = _now() if patch.checked else None
    if patch.note is not None:
        item.note = patch.note

    await session.commit()
    await session.refresh(item)
    logger.info(
        "checklist_toggle",
        extra={
            "extra": {
                "order_id": order_id,
                "run_id": item.run_id,
                "run_item_id": item.run_item_id,
                "checked": item.checked,
            }
        },
    )
    return item


async def complete_checklist(session: AsyncSession, order_id: str) -> ChecklistRun | None:
    run = await find_run_by_order(session, order_id)
    if run is None:
        return None
    if run.status == STATUS_COMPLETED:
        return run

    missing = [
        item
        for item in run.items
        if item.template_item.required and not item.checked
    ]
    if missing:
        missing_ids = [itm.run_item_id for itm in missing]
        raise ValueError(f"Required items not completed: {', '.join(missing_ids)}")

    run.status = STATUS_COMPLETED
    run.completed_at = _now()
    await session.commit()
    await session.refresh(run)
    logger.info(
        "checklist_complete",
        extra={"extra": {"order_id": order_id, "run_id": run.run_id}},
    )
    return run


def serialize_run(run: ChecklistRun) -> checklist_schemas.ChecklistRunResponse:
    template = run.template
    items: list[checklist_schemas.ChecklistRunItemResponse] = []
    for item in sorted(run.items, key=lambda itm: itm.template_item.position):
        template_item = item.template_item
        items.append(
            checklist_schemas.ChecklistRunItemResponse(
                run_item_id=item.run_item_id,
                template_item_id=item.template_item_id,
                label=template_item.label,
                phase=template_item.phase,
                required=template_item.required,
                position=template_item.position,
                checked=item.checked,
                checked_at=item.checked_at,
                note=item.note,
            )
        )
    return checklist_schemas.ChecklistRunResponse(
        run_id=run.run_id,
        order_id=run.order_id,
        template_id=template.template_id,
        template_name=template.name,
        template_version=template.version,
        service_type=template.service_type,
        status=run.status,
        created_at=run.created_at,
        completed_at=run.completed_at,
        items=items,
    )
