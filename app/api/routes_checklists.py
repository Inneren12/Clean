import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes_admin import require_admin, verify_admin_or_dispatcher
from app.dependencies import get_db_session
from app.domain.checklists import schemas as checklist_schemas
from app.domain.checklists import service as checklist_service

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get(
    "/v1/admin/checklists/templates",
    response_model=list[checklist_schemas.ChecklistTemplateResponse],
    status_code=status.HTTP_200_OK,
)
async def list_templates(
    session: AsyncSession = Depends(get_db_session),
    identity=Depends(require_admin),
) -> list[checklist_schemas.ChecklistTemplateResponse]:
    templates = await checklist_service.list_templates(session)
    responses: list[checklist_schemas.ChecklistTemplateResponse] = []
    for template in templates:
        responses.append(
            checklist_schemas.ChecklistTemplateResponse(
                template_id=template.template_id,
                name=template.name,
                service_type=template.service_type,
                version=template.version,
                is_active=template.is_active,
                items=[
                    checklist_schemas.ChecklistTemplateItemResponse(
                        item_id=item.item_id,
                        position=item.position,
                        label=item.label,
                        phase=item.phase,
                        required=item.required,
                    )
                    for item in template.items
                ],
            )
        )
    return responses


@router.post(
    "/v1/admin/checklists/templates",
    response_model=checklist_schemas.ChecklistTemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_template(
    request: checklist_schemas.ChecklistTemplateRequest,
    session: AsyncSession = Depends(get_db_session),
    identity=Depends(require_admin),
) -> checklist_schemas.ChecklistTemplateResponse:
    template = await checklist_service.create_template(session, request)
    return checklist_schemas.ChecklistTemplateResponse(
        template_id=template.template_id,
        name=template.name,
        service_type=template.service_type,
        version=template.version,
        is_active=template.is_active,
        items=[
            checklist_schemas.ChecklistTemplateItemResponse(
                item_id=item.item_id,
                position=item.position,
                label=item.label,
                phase=item.phase,
                required=item.required,
            )
            for item in template.items
        ],
    )


@router.put(
    "/v1/admin/checklists/templates/{template_id}",
    response_model=checklist_schemas.ChecklistTemplateResponse,
    status_code=status.HTTP_200_OK,
)
async def update_template(
    template_id: int,
    request: checklist_schemas.ChecklistTemplateUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
    identity=Depends(require_admin),
) -> checklist_schemas.ChecklistTemplateResponse:
    template = await checklist_service.update_template(session, template_id, request)
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    return checklist_schemas.ChecklistTemplateResponse(
        template_id=template.template_id,
        name=template.name,
        service_type=template.service_type,
        version=template.version,
        is_active=template.is_active,
        items=[
            checklist_schemas.ChecklistTemplateItemResponse(
                item_id=item.item_id,
                position=item.position,
                label=item.label,
                phase=item.phase,
                required=item.required,
            )
            for item in template.items
        ],
    )


@router.delete(
    "/v1/admin/checklists/templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_template(
    template_id: int,
    session: AsyncSession = Depends(get_db_session),
    identity=Depends(require_admin),
) -> None:
    try:
        deleted = await checklist_service.delete_template(session, template_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")


@router.post(
    "/v1/orders/{order_id}/checklist/init",
    response_model=checklist_schemas.ChecklistRunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def init_checklist(
    order_id: str,
    request: checklist_schemas.ChecklistInitRequest,
    session: AsyncSession = Depends(get_db_session),
    identity=Depends(verify_admin_or_dispatcher),
) -> checklist_schemas.ChecklistRunResponse:
    try:
        run = await checklist_service.init_checklist(
            session, order_id=order_id, template_id=request.template_id, service_type=request.service_type
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return checklist_service.serialize_run(run)


@router.get(
    "/v1/orders/{order_id}/checklist",
    response_model=checklist_schemas.ChecklistRunResponse,
    status_code=status.HTTP_200_OK,
)
async def get_checklist(
    order_id: str,
    session: AsyncSession = Depends(get_db_session),
    identity=Depends(verify_admin_or_dispatcher),
) -> checklist_schemas.ChecklistRunResponse:
    run = await checklist_service.find_run_by_order(session, order_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Checklist not found")
    return checklist_service.serialize_run(run)


@router.patch(
    "/v1/orders/{order_id}/checklist/items/{run_item_id}",
    response_model=checklist_schemas.ChecklistRunResponse,
    status_code=status.HTTP_200_OK,
)
async def update_checklist_item(
    order_id: str,
    run_item_id: str,
    patch: checklist_schemas.ChecklistRunItemPatch,
    session: AsyncSession = Depends(get_db_session),
    identity=Depends(verify_admin_or_dispatcher),
) -> checklist_schemas.ChecklistRunResponse:
    try:
        item = await checklist_service.toggle_item(session, order_id, run_item_id, patch)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Checklist item not found")
    run = await checklist_service.find_run_by_order(session, order_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Checklist not found")
    return checklist_service.serialize_run(run)


@router.post(
    "/v1/orders/{order_id}/checklist/complete",
    response_model=checklist_schemas.ChecklistRunResponse,
    status_code=status.HTTP_200_OK,
)
async def complete_checklist(
    order_id: str,
    session: AsyncSession = Depends(get_db_session),
    identity=Depends(verify_admin_or_dispatcher),
) -> checklist_schemas.ChecklistRunResponse:
    try:
        run = await checklist_service.complete_checklist(session, order_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Checklist not found")
    return checklist_service.serialize_run(run)


@router.get(
    "/v1/admin/checklists",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def admin_checklists_page(
    session: AsyncSession = Depends(get_db_session),
    identity=Depends(require_admin),
):
    templates = await checklist_service.list_templates(session)
    rows = ["<h1>Checklist Templates</h1>"]
    if not templates:
        rows.append("<p>No templates defined.</p>")
    for template in templates:
        rows.append("<div style='margin-bottom:16px'>")
        rows.append(
            f"<strong>{template.name}</strong> (service: {template.service_type or 'default'}, version: {template.version})"
        )
        rows.append("<ul>")
        for item in template.items:
            flag = "(required)" if item.required else ""
            rows.append(f"<li>#{item.position} [{item.phase}] {item.label} {flag}</li>")
        rows.append("</ul>")
        rows.append("</div>")
    content = "".join(rows)
    return HTMLResponse(content=content)
