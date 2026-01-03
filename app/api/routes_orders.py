import logging
import time
import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import entitlements
from app.api.admin_auth import (
    AdminIdentity,
    AdminPermission,
    AdminRole,
    ROLE_PERMISSIONS,
    require_admin,
    require_dispatch,
)
from app.domain.addons import schemas as addon_schemas
from app.domain.addons import service as addon_service
from app.api.photo_tokens import (
    build_signed_photo_response,
    normalize_variant,
    verify_photo_download_token,
)
from app.dependencies import get_db_session
from app.domain.bookings import photos_service
from app.domain.bookings import schemas as booking_schemas
from app.domain.admin_audit import service as audit_service
from app.domain.reason_logs import schemas as reason_schemas
from app.domain.reason_logs import service as reason_service
from app.infra.storage import resolve_storage_backend
from app.infra.storage.backends import CloudflareImagesStorageBackend
from app.domain.saas import billing_service
from app.settings import settings

router = APIRouter()
logger = logging.getLogger(__name__)


def _storage_backend(request: Request):
    return resolve_storage_backend(request.app.state)


def _order_org_id(identity: AdminIdentity, request: Request) -> uuid.UUID:
    saas_identity = getattr(request.state, "saas_identity", None)
    candidates = [
        getattr(request.state, "current_org_id", None),
        getattr(identity, "org_id", None),
        getattr(saas_identity, "org_id", None),
    ]
    for value in candidates:
        if value:
            try:
                return uuid.UUID(str(value))
            except Exception:  # noqa: BLE001
                continue
    return entitlements.resolve_org_id(request)


def _order_addon_response(model) -> addon_schemas.OrderAddonResponse:
    definition = getattr(model, "definition", None)
    return addon_schemas.OrderAddonResponse(
        order_addon_id=model.order_addon_id,
        order_id=model.order_id,
        addon_id=model.addon_id,
        code=getattr(definition, "code", str(model.addon_id)),
        name=getattr(definition, "name", f"Addon {model.addon_id}"),
        qty=model.qty,
        unit_price_cents=model.unit_price_cents_snapshot,
        minutes=model.minutes_snapshot,
        created_at=model.created_at,
    )


@router.patch(
    "/v1/orders/{order_id}/addons",
    response_model=list[addon_schemas.OrderAddonResponse],
)
async def update_order_addons(
    order_id: str,
    payload: addon_schemas.OrderAddonUpdateRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    _identity: AdminIdentity = Depends(require_dispatch),
) -> list[addon_schemas.OrderAddonResponse]:
    org_id = _order_org_id(_identity, request)
    await photos_service.fetch_order(session, order_id, org_id)
    try:
        await addon_service.set_order_addons(session, order_id, payload.addons)
    except ValueError as exc:
        status_code = status.HTTP_404_NOT_FOUND if "not found" in str(exc).lower() else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    await session.commit()
    addons = await addon_service.list_order_addons(session, order_id)
    return [_order_addon_response(addon) for addon in addons]


@router.get(
    "/v1/orders/{order_id}/addons",
    response_model=list[addon_schemas.OrderAddonResponse],
)
async def list_order_addons(
    order_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    _identity: AdminIdentity = Depends(require_dispatch),
) -> list[addon_schemas.OrderAddonResponse]:
    org_id = _order_org_id(_identity, request)
    await photos_service.fetch_order(session, order_id, org_id)
    addons = await addon_service.list_order_addons(session, order_id)
    return [_order_addon_response(addon) for addon in addons]


@router.post(
    "/v1/orders/{order_id}/reasons",
    response_model=reason_schemas.ReasonResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_reason_log(
    order_id: str,
    payload: reason_schemas.ReasonCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    identity: AdminIdentity = Depends(require_dispatch),
) -> reason_schemas.ReasonResponse:
    org_id = _order_org_id(identity, request)
    await photos_service.fetch_order(session, order_id, org_id)
    try:
        reason = await reason_service.create_reason(
            session,
            order_id,
            kind=payload.kind,
            code=payload.code,
            note=payload.note,
            created_by=identity.username or identity.role.value,
            time_entry_id=payload.time_entry_id,
            invoice_item_id=payload.invoice_item_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await session.commit()
    await session.refresh(reason)
    return reason_schemas.ReasonResponse.from_model(reason)


@router.get(
    "/v1/orders/{order_id}/reasons",
    response_model=reason_schemas.ReasonListResponse,
)
async def list_order_reasons(
    order_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    identity: AdminIdentity = Depends(require_dispatch),
) -> reason_schemas.ReasonListResponse:
    org_id = _order_org_id(identity, request)
    await photos_service.fetch_order(session, order_id, org_id)
    reasons = await reason_service.list_reasons_for_order(session, order_id)
    return reason_schemas.ReasonListResponse(
        reasons=[reason_schemas.ReasonResponse.from_model(reason) for reason in reasons]
    )


@router.patch(
    "/v1/orders/{order_id}/consent_photos",
    response_model=booking_schemas.ConsentPhotosResponse,
)
async def update_photo_consent(
    order_id: str,
    payload: booking_schemas.ConsentPhotosUpdateRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    identity: AdminIdentity = Depends(require_dispatch),
) -> booking_schemas.ConsentPhotosResponse:
    org_id = _order_org_id(identity, request)
    order = await photos_service.update_consent(
        session, order_id, payload.consent_photos, org_id=org_id
    )
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
    request: Request = None,
    session: AsyncSession = Depends(get_db_session),
    identity: AdminIdentity = Depends(require_dispatch),
) -> booking_schemas.OrderPhotoResponse:
    org_id = _order_org_id(identity, request)
    order = await photos_service.fetch_order(session, order_id, org_id)
    parsed_phase = booking_schemas.PhotoPhase.from_any_case(phase)

    contents = await file.read()
    size_bytes = len(contents or b"")
    await entitlements.enforce_storage_entitlement(request, size_bytes, session=session)
    await file.seek(0)

    if admin_override:
        has_admin_permission = AdminPermission.ADMIN in ROLE_PERMISSIONS.get(identity.role, set())
        is_owner_or_admin = identity.role in {AdminRole.ADMIN, AdminRole.OWNER}
        if not (has_admin_permission or is_owner_or_admin):
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

    uploaded_by = identity.role.value or identity.username
    storage = _storage_backend(request)
    photo = await photos_service.save_photo(
        session, order, file, parsed_phase, uploaded_by, org_id, storage
    )
    if entitlements.has_tenant_identity(request):
        await billing_service.record_usage_event(
            session,
            org_id,
            metric="storage_bytes",
            quantity=photo.size_bytes,
            resource_id=photo.photo_id,
        )
    await session.commit()
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
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    identity: AdminIdentity = Depends(require_dispatch),
) -> booking_schemas.OrderPhotoListResponse:
    # Admin/dispatcher can list photos regardless of consent_photos status
    # This allows viewing admin_override uploads even when consent is false
    org_id = _order_org_id(identity, request)
    await photos_service.fetch_order(session, order_id, org_id)
    photos = await photos_service.list_photos(session, order_id, org_id)
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
                review_status=booking_schemas.PhotoReviewStatus.from_any_case(
                    photo.review_status
                ),
                review_comment=photo.review_comment,
                reviewed_by=photo.reviewed_by,
                reviewed_at=photo.reviewed_at,
                needs_retake=bool(photo.needs_retake),
            )
            for photo in photos
        ]
    )


@router.get(
    "/v1/orders/{order_id}/photos/{photo_id}/signed_url",
    response_model=booking_schemas.SignedUrlResponse,
)
async def signed_order_photo_url(
    order_id: str,
    photo_id: str,
    request: Request,
    variant: str | None = Query(None),
    session: AsyncSession = Depends(get_db_session),
    identity: AdminIdentity = Depends(require_dispatch),
) -> booking_schemas.SignedUrlResponse:
    _ = identity
    org_id = _order_org_id(identity, request)
    photo = await photos_service.get_photo(session, order_id, photo_id, org_id)
    storage = _storage_backend(request)
    normalized_variant = normalize_variant(variant)
    return await build_signed_photo_response(
        photo, request, storage, org_id, variant=normalized_variant
    )


@router.post(
    "/v1/orders/{order_id}/photos/{photo_id}/review",
    response_model=booking_schemas.OrderPhotoResponse,
)
async def review_order_photo(
    order_id: str,
    photo_id: str,
    payload: booking_schemas.PhotoReviewUpdateRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    identity: AdminIdentity = Depends(require_admin),
) -> booking_schemas.OrderPhotoResponse:
    org_id = _order_org_id(identity, request)
    photo = await photos_service.review_photo(
        session,
        order_id,
        photo_id,
        org_id=org_id,
        reviewer=identity.username,
        status=payload.review_status.value,
        comment=payload.review_comment,
        needs_retake=payload.needs_retake,
    )

    audit_log = await audit_service.record_action(
        session,
        identity=identity,
        action="ORDER_PHOTO_REVIEW",
        resource_type="photo",
        resource_id=photo.photo_id,
        before=None,
        after={
            "order_id": order_id,
            "review_status": photo.review_status,
            "needs_retake": photo.needs_retake,
        },
    )
    audit_log.org_id = org_id
    await session.commit()

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
        review_status=booking_schemas.PhotoReviewStatus.from_any_case(photo.review_status),
        review_comment=photo.review_comment,
        reviewed_by=photo.reviewed_by,
        reviewed_at=photo.reviewed_at,
        needs_retake=bool(photo.needs_retake),
    )


@router.get(
    "/v1/orders/{order_id}/photos/{photo_id}/signed-download",
    name="signed_download_order_photo",
)
async def signed_download_order_photo(
    order_id: str,
    photo_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    token = request.query_params.get("token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    claims = await verify_photo_download_token(token, user_agent=request.headers.get("user-agent"))
    if claims.order_id != order_id or claims.photo_id != photo_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")

    photo = await photos_service.get_photo(session, order_id, photo_id, claims.org_id)
    if photo.order_id != claims.order_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")

    storage = _storage_backend(request)
    key = photos_service.storage_key_for_photo(photo, claims.org_id)
    ttl = min(settings.photo_url_ttl_seconds, max(claims.exp - int(time.time()), 1))

    if storage.supports_direct_io():
        file_path = storage.path_for(key) if hasattr(storage, "path_for") else None
        if not file_path or not file_path.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File missing")
        response = FileResponse(
            path=file_path,
            media_type=photo.content_type,
            filename=photo.original_filename or photo.filename,
        )
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    signed_url = await storage.generate_signed_get_url(
        key=key,
        expires_in=ttl,
        resource_url=str(request.url),
        variant=claims.variant,
    )
    response = RedirectResponse(
        url=signed_url, status_code=settings.photo_download_redirect_status
    )
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.get(
    "/v1/orders/{order_id}/photos/{photo_id}/download",
    response_class=FileResponse,
)
async def download_order_photo(
    order_id: str,
    photo_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    identity: AdminIdentity = Depends(require_dispatch),
) -> Response:
    _ = identity
    org_id = _order_org_id(identity, request)
    photo = await photos_service.get_photo(session, order_id, photo_id, org_id)
    storage = _storage_backend(request)
    key = photos_service.storage_key_for_photo(photo, org_id)
    ttl = settings.photo_url_ttl_seconds

    signature = request.query_params.get("sig")
    expires_raw = request.query_params.get("exp")
    has_valid_token = storage.validate_signed_get_url(key=key, url=str(request.url))

    if settings.app_env != "dev" and not has_valid_token:
        download_url = str(request.url_for("download_order_photo", order_id=order_id, photo_id=photo_id))
        signed_url = await storage.generate_signed_get_url(
            key=key, expires_in=ttl, resource_url=download_url
        )
        return RedirectResponse(url=signed_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    if not storage.supports_direct_io():
        download_url = str(request.url_for("download_order_photo", order_id=order_id, photo_id=photo_id))
        signed_url = await storage.generate_signed_get_url(
            key=key, expires_in=ttl, resource_url=download_url
        )
        return RedirectResponse(url=signed_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    file_path = storage.path_for(key) if hasattr(storage, "path_for") else None
    if not file_path or not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File missing")

    if (signature or expires_raw) and not has_valid_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature")

    logger.info(
        "order_photo_download",
        extra={
            "extra": {
                "order_id": order_id,
                "photo_id": photo_id,
                "requested_by": identity.role.value,
            }
        },
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
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    _admin: AdminIdentity = Depends(require_admin),
) -> None:
    storage = _storage_backend(request)
    org_id = _order_org_id(_admin, request)
    photo = await photos_service.delete_photo(
        session,
        order_id,
        photo_id,
        storage=storage,
        org_id=org_id,
        record_usage=entitlements.has_tenant_identity(request),
    )
    await session.commit()


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
    org_id = _order_org_id(_admin, request)
    photos = await photos_service.list_photos(session, order_id, org_id)
    storage = _storage_backend(request)
    items: list[str] = []
    for photo in photos:
        signed = await build_signed_photo_response(photo, request, storage, org_id)
        download_url = signed.url
        thumbnail_url: str | None = None
        thumbnail_variant = normalize_variant(
            settings.cf_images_thumbnail_variant or settings.cf_images_default_variant
        )
        if thumbnail_variant and isinstance(storage, CloudflareImagesStorageBackend):
            thumbnail_signed = await build_signed_photo_response(
                photo,
                request,
                storage,
                org_id,
                variant=thumbnail_variant,
            )
            thumbnail_url = thumbnail_signed.url
        items.append(
            f"<li><strong>{photo.phase}</strong> - {photo.original_filename or photo.filename} "
            f"({photo.size_bytes} bytes) - <a href=\"{download_url}\">Download</a>"
            + (f" - <a href=\"{thumbnail_url}\">Thumbnail</a>" if thumbnail_url else "")
            + "</li>"
        )
    body = "<p>No photos yet.</p>" if not items else "<ul>" + "".join(items) + "</ul>"
    html = f"<h2>Order {order_id} Photos</h2>{body}"
    return HTMLResponse(content=html)
