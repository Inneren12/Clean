import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes_admin import AdminIdentity, require_admin, verify_admin_or_dispatcher
from app.dependencies import get_db_session
from app.domain.bookings import photos_service
from app.domain.bookings import schemas as booking_schemas

router = APIRouter()
logger = logging.getLogger(__name__)
security = HTTPBasic(auto_error=False)


async def optional_identity(
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> AdminIdentity | None:
    if credentials is None:
        return None
    return await verify_admin_or_dispatcher(credentials)


@router.patch(
    "/v1/orders/{order_id}/consent_photos",
    response_model=booking_schemas.ConsentPhotosResponse,
)
async def update_photo_consent(
    order_id: str,
    payload: booking_schemas.ConsentPhotosUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
    _identity: AdminIdentity | None = Depends(optional_identity),
) -> booking_schemas.ConsentPhotosResponse:
    order = await photos_service.update_consent(session, order_id, payload.consent_photos)
    return booking_schemas.ConsentPhotosResponse(
        order_id=order.booking_id, consent_photos=order.consent_photos
    )


@router.post(
    "/v1/orders/{order_id}/photos",
    status_code=status.HTTP_201_CREATED,
    response_model=booking_schemas.OrderPhotoResponse,
)
async def upload_order_photo(
    order_id: str,
    phase: str = Form(...),
    admin_override: bool = Form(False),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db_session),
    identity: AdminIdentity | None = Depends(optional_identity),
) -> booking_schemas.OrderPhotoResponse:
    order = await photos_service.fetch_order(session, order_id)
    parsed_phase = booking_schemas.PhotoPhase.from_any_case(phase)

    if admin_override and (identity is None or identity.role != "admin"):
        logger.info(
            "order_photo_denied",
            extra={"extra": {"order_id": order_id, "reason": "admin_override_required"}},
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin override required")

    if not order.consent_photos and not admin_override:
        logger.info(
            "order_photo_denied",
            extra={"extra": {"order_id": order_id, "reason": "consent_required"}},
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Photo consent not granted")

    uploaded_by = "admin" if identity else "client"
    photo = await photos_service.save_photo(session, order, file, parsed_phase, uploaded_by)
    return booking_schemas.OrderPhotoResponse(
        photo_id=photo.photo_id,
        order_id=photo.order_id,
        phase=booking_schemas.PhotoPhase(photo.phase),
        filename=photo.filename,
        original_filename=photo.original_filename,
        content_type=photo.content_type,
        size_bytes=photo.size_bytes,
        sha256=photo.sha256,
        uploaded_by=photo.uploaded_by,
        created_at=photo.created_at,
    )


@router.get(
    "/v1/orders/{order_id}/photos",
    response_model=booking_schemas.OrderPhotoListResponse,
)
async def list_order_photos(
    order_id: str,
    session: AsyncSession = Depends(get_db_session),
    identity: AdminIdentity | None = Depends(optional_identity),
) -> booking_schemas.OrderPhotoListResponse:
    order = await photos_service.fetch_order(session, order_id)
    if not order.consent_photos and identity is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Photo consent not granted")

    photos = await photos_service.list_photos(session, order_id)
    return booking_schemas.OrderPhotoListResponse(
        photos=[
            booking_schemas.OrderPhotoResponse(
                photo_id=photo.photo_id,
                order_id=photo.order_id,
                phase=booking_schemas.PhotoPhase(photo.phase),
                filename=photo.filename,
                original_filename=photo.original_filename,
                content_type=photo.content_type,
                size_bytes=photo.size_bytes,
                sha256=photo.sha256,
                uploaded_by=photo.uploaded_by,
                created_at=photo.created_at,
            )
            for photo in photos
        ]
    )


@router.get(
    "/v1/orders/{order_id}/photos/{photo_id}/download",
    response_class=FileResponse,
)
async def download_order_photo(
    order_id: str,
    photo_id: str,
    session: AsyncSession = Depends(get_db_session),
    identity: AdminIdentity = Depends(verify_admin_or_dispatcher),
) -> FileResponse:
    _ = identity
    photo = await photos_service.get_photo(session, order_id, photo_id)
    file_path = photos_service.photo_path(photo)
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File missing")

    logger.info(
        "order_photo_download",
        extra={"extra": {"order_id": order_id, "photo_id": photo_id, "requested_by": identity.role}},
    )
    return FileResponse(
        path=file_path,
        media_type=photo.content_type,
        filename=photo.original_filename or photo.filename,
    )


@router.delete("/v1/orders/{order_id}/photos/{photo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_order_photo(
    order_id: str,
    photo_id: str,
    session: AsyncSession = Depends(get_db_session),
    _admin: AdminIdentity = Depends(require_admin),
) -> None:
    await photos_service.delete_photo(session, order_id, photo_id)


@router.get(
    "/v1/admin/orders/{order_id}/photos",
    response_class=HTMLResponse,
)
async def admin_order_gallery(
    order_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    _admin: AdminIdentity = Depends(require_admin),
) -> HTMLResponse:
    photos = await photos_service.list_photos(session, order_id)
    items: list[str] = []
    for photo in photos:
        download_url = request.url_for(
            "download_order_photo", order_id=order_id, photo_id=photo.photo_id
        )
        items.append(
            f"<li><strong>{photo.phase}</strong> - {photo.original_filename or photo.filename} "
            f"({photo.size_bytes} bytes) - <a href=\"{download_url}\">Download</a></li>"
        )
    body = "<p>No photos yet.</p>" if not items else "<ul>" + "".join(items) + "</ul>"
    html = f"<h2>Order {order_id} Photos</h2>{body}"
    return HTMLResponse(content=html)
