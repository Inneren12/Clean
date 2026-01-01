import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import entitlements
from app.api.photo_tokens import build_signed_photo_response
from app.dependencies import get_db_session
from app.domain.bookings.db_models import Booking, OrderPhoto
from app.domain.bookings import photos_service
from app.domain.bookings import schemas as booking_schemas
from app.domain.bookings import service as booking_service
from app.domain.clients import schemas as client_schemas
from app.domain.clients import service as client_service
from app.domain.invoices.db_models import Invoice
from app.domain.leads.db_models import Lead
from app.domain.subscriptions import schemas as subscription_schemas
from app.domain.subscriptions import service as subscription_service
from app.domain.subscriptions.db_models import Subscription
from app.settings import settings
from app.infra.storage import resolve_storage_backend

router = APIRouter()
logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "client_session"


async def _get_identity_from_token(token: str | None) -> client_schemas.ClientIdentity:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        result = client_service.verify_magic_token(token, secret=settings.client_portal_secret)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    return client_schemas.ClientIdentity(
        client_id=result.client_id, email=result.email, issued_at=result.issued_at
    )


async def require_identity(request: Request) -> client_schemas.ClientIdentity:
    token = request.cookies.get(SESSION_COOKIE_NAME) or request.headers.get("Authorization")
    if token and token.startswith("Bearer "):
        token = token.split(" ", 1)[1]
    identity = await _get_identity_from_token(token)
    request.state.current_org_id = getattr(request.state, "current_org_id", None) or settings.default_org_id
    return identity


@router.get("/client/org-context")
async def client_org_context(
    request: Request, identity: client_schemas.ClientIdentity = Depends(require_identity)
) -> JSONResponse:
    return JSONResponse({"org_id": str(getattr(request.state, "current_org_id", None))})


def _magic_link_destination(request: Request) -> str:
    public_base = getattr(settings, "public_base_url", None)
    base_url = settings.client_portal_base_url or public_base
    if not base_url:
        base_url = str(request.base_url).rstrip("/")
    return f"{base_url}/client/login/callback"


def _render_dashboard(orders: Iterable[client_schemas.ClientOrderSummary]) -> str:
    items = "".join(
        f"<li><a href='/client/orders/{order.order_id}'>{order.order_id}</a> — {order.status}</li>"
        for order in orders
    )
    if not items:
        items = "<li>No orders yet.</li>"
    return f"""
    <html>
      <body>
        <h1>Your orders</h1>
        <ul>{items}</ul>
      </body>
    </html>
    """


@router.post("/client/login/request")
async def request_login(
    payload: client_schemas.LoginRequest, request: Request, session: AsyncSession = Depends(get_db_session)
) -> JSONResponse:
    client = await client_service.get_or_create_client(
        session, payload.email, commit=False
    )
    await session.commit()
    await client_service.attach_client_to_orders(session, client)

    token = client_service.issue_magic_token(
        email=client.email,
        client_id=client.client_id,
        secret=settings.client_portal_secret,
        ttl_minutes=settings.client_portal_token_ttl_minutes,
    )
    callback_url = f"{_magic_link_destination(request)}?" + urlencode({"token": token})
    link_body = (
        "Hi!\n\n"
        "Use the link below to access your cleaning orders.\n\n"
        f"{callback_url}\n\n"
        "If you did not request this link, you can ignore this email."
    )
    email_adapter = getattr(request.app.state, "email_adapter", None)
    if email_adapter:
        try:
            await email_adapter.send_email(
                recipient=client.email, subject="Your client portal link", body=link_body
            )
        except Exception:  # noqa: BLE001
            logger.warning("client_login_email_failed", extra={"extra": {"email": client.email}})

    logger.info("client_login_requested", extra={"extra": {"email": client.email}})
    return JSONResponse({"status": "ok"})


@router.get("/client/login/callback")
async def login_callback(token: str, request: Request) -> HTMLResponse:
    identity = await _get_identity_from_token(token)
    secure = settings.app_env != "dev" or request.url.scheme == "https"
    response = HTMLResponse(
        "<html><body><p>Login successful. Continue to <a href='/client'>your dashboard</a>.</p></body></html>"
    )
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=int(timedelta(minutes=settings.client_portal_token_ttl_minutes).total_seconds()),
    )
    logger.info(
        "client_login_success", extra={"extra": {"client_id": identity.client_id, "email": identity.email}}
    )
    return response


@router.get("/client", response_class=HTMLResponse)
async def dashboard(
    identity: client_schemas.ClientIdentity = Depends(require_identity),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    orders = await _list_orders(session, identity.client_id)
    return HTMLResponse(_render_dashboard(orders))


async def _list_orders(session: AsyncSession, client_id: str) -> list[client_schemas.ClientOrderSummary]:
    stmt = (
        select(Booking)
        .where(Booking.client_id == client_id)
        .order_by(Booking.starts_at.desc())
    )
    result = await session.execute(stmt)
    bookings = result.scalars().all()
    return [
        client_schemas.ClientOrderSummary(
            order_id=booking.booking_id,
            status=booking.status,
            starts_at=booking.starts_at,
            duration_minutes=booking.duration_minutes,
        )
        for booking in bookings
    ]


def _subscription_response(model: Subscription) -> subscription_schemas.SubscriptionResponse:
    return subscription_schemas.SubscriptionResponse(
        subscription_id=model.subscription_id,
        client_id=model.client_id,
        status=model.status,
        frequency=model.frequency,
        start_date=model.start_date,
        next_run_at=model.next_run_at,
        preferred_weekday=model.preferred_weekday,
        preferred_day_of_month=model.preferred_day_of_month,
        base_service_type=model.base_service_type,
        base_price=model.base_price,
        created_at=model.created_at,
    )


def _client_booking_response(model: Booking) -> booking_schemas.ClientBookingResponse:
    return booking_schemas.ClientBookingResponse(
        booking_id=model.booking_id,
        status=model.status,
        starts_at=model.starts_at if model.starts_at.tzinfo else model.starts_at.replace(tzinfo=timezone.utc),
        duration_minutes=model.duration_minutes,
        lead_id=model.lead_id,
        policy_snapshot=model.policy_snapshot,
        deposit_required=model.deposit_required,
        deposit_cents=model.deposit_cents,
        deposit_policy=model.deposit_policy,
        deposit_status=model.deposit_status,
        cancellation_exception=model.cancellation_exception,
    )


async def _get_client_subscription(
    session: AsyncSession, subscription_id: str, client_id: str
) -> Subscription:
    subscription = await session.get(Subscription, subscription_id)
    if not subscription or subscription.client_id != client_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")
    return subscription


@router.post(
    "/client/subscriptions",
    response_model=subscription_schemas.SubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_subscription(
    payload: subscription_schemas.SubscriptionCreateRequest,
    identity: client_schemas.ClientIdentity = Depends(require_identity),
    session: AsyncSession = Depends(get_db_session),
) -> subscription_schemas.SubscriptionResponse:
    subscription = await subscription_service.create_subscription(
        session, identity.client_id, payload
    )
    await session.commit()
    await session.refresh(subscription)
    return _subscription_response(subscription)


@router.get("/client/subscriptions", response_model=list[subscription_schemas.SubscriptionResponse])
async def list_subscriptions(
    identity: client_schemas.ClientIdentity = Depends(require_identity),
    session: AsyncSession = Depends(get_db_session),
) -> list[subscription_schemas.SubscriptionResponse]:
    subscriptions = await subscription_service.list_client_subscriptions(
        session, identity.client_id
    )
    return [_subscription_response(sub) for sub in subscriptions]


@router.patch(
    "/client/subscriptions/{subscription_id}",
    response_model=subscription_schemas.SubscriptionResponse,
)
async def update_subscription_status(
    subscription_id: str,
    payload: subscription_schemas.SubscriptionUpdateRequest,
    identity: client_schemas.ClientIdentity = Depends(require_identity),
    session: AsyncSession = Depends(get_db_session),
) -> subscription_schemas.SubscriptionResponse:
    subscription = await _get_client_subscription(session, subscription_id, identity.client_id)
    await subscription_service.update_subscription_status(session, subscription, payload.status)
    await session.commit()
    await session.refresh(subscription)
    return _subscription_response(subscription)


@router.get("/client/orders")
async def list_orders(
    identity: client_schemas.ClientIdentity = Depends(require_identity),
    session: AsyncSession = Depends(get_db_session),
) -> list[client_schemas.ClientOrderSummary]:
    logger.info("client_portal_access", extra={"extra": {"client_id": identity.client_id, "path": "/client/orders"}})
    return await _list_orders(session, identity.client_id)


async def _get_client_booking(session: AsyncSession, order_id: str, client_id: str) -> Booking:
    booking = await session.get(Booking, order_id)
    if not booking or booking.client_id != client_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return booking


async def _get_owned_booking(
    session: AsyncSession, booking_id: str, identity: client_schemas.ClientIdentity
) -> Booking:
    booking = await session.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    if booking.client_id:
        if booking.client_id != identity.client_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
        return booking

    if booking.lead_id:
        lead = await session.get(Lead, booking.lead_id)
        if lead and lead.email and lead.email.lower() == identity.email.lower():
            booking.client_id = identity.client_id
            await session.flush()
            return booking

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")


@router.get("/client/orders/{order_id}")
async def order_detail(
    order_id: str,
    identity: client_schemas.ClientIdentity = Depends(require_identity),
    session: AsyncSession = Depends(get_db_session),
) -> client_schemas.ClientOrderDetail:
    booking = await _get_client_booking(session, order_id, identity.client_id)
    photos_count = await session.scalar(
        select(func.count(OrderPhoto.photo_id)).where(OrderPhoto.order_id == booking.booking_id)
    )
    return client_schemas.ClientOrderDetail(
        order_id=booking.booking_id,
        status=booking.status,
        starts_at=booking.starts_at,
        duration_minutes=booking.duration_minutes,
        deposit_required=booking.deposit_required,
        deposit_status=booking.deposit_status,
        pay_link=f"https://pay.example.com/orders/{booking.booking_id}",
        photos_available=bool(booking.consent_photos),
        photos_count=photos_count or 0,
    )


@router.get(
    "/client/orders/{order_id}/photos/{photo_id}/signed_url",
    response_model=booking_schemas.SignedUrlResponse,
)
async def client_photo_signed_url(
    order_id: str,
    photo_id: str,
    request: Request,
    identity: client_schemas.ClientIdentity = Depends(require_identity),
    session: AsyncSession = Depends(get_db_session),
) -> booking_schemas.SignedUrlResponse:
    booking = await _get_client_booking(session, order_id, identity.client_id)
    if not booking.consent_photos:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Photo access not granted")
    photo = await photos_service.get_photo(session, order_id, photo_id)
    if photo.order_id != booking.booking_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")
    storage = resolve_storage_backend(request.app.state)
    org_id = entitlements.resolve_org_id(request)
    return await build_signed_photo_response(photo, request, storage, org_id)


@router.get("/client/invoices/{invoice_id}")
async def invoice_detail(
    invoice_id: str,
    identity: client_schemas.ClientIdentity = Depends(require_identity),
    session: AsyncSession = Depends(get_db_session),
) -> client_schemas.ClientInvoiceResponse:
    invoice = await session.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    if invoice.order_id:
        booking = await _get_client_booking(session, invoice.order_id, identity.client_id)
        order_id = booking.booking_id
    elif invoice.customer_id:
        lead = await session.get(Lead, invoice.customer_id)
        if not lead or lead.email.lower() != identity.email.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
        order_id = None
    else:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    return client_schemas.ClientInvoiceResponse(
        invoice_id=invoice.invoice_id,
        invoice_number=invoice.invoice_number,
        status=invoice.status,
        total_cents=invoice.total_cents,
        currency=invoice.currency,
        issued_at=datetime.combine(invoice.issue_date, datetime.min.time(), tzinfo=timezone.utc),
        order_id=order_id,
    )


@router.post("/client/orders/{order_id}/repeat", status_code=status.HTTP_201_CREATED)
async def repeat_order(
    order_id: str,
    identity: client_schemas.ClientIdentity = Depends(require_identity),
    session: AsyncSession = Depends(get_db_session),
) -> client_schemas.ClientOrderSummary:
    original = await _get_client_booking(session, order_id, identity.client_id)
    new_start = original.starts_at + timedelta(days=7)

    # Fetch the lead to evaluate deposit policy for the new booking date
    lead = None
    if original.lead_id:
        lead = await session.get(Lead, original.lead_id)

    # Re-evaluate deposit policy based on the new start time and lead details
    deposit_decision = await booking_service.evaluate_deposit_policy(
        session=session,
        lead=lead,
        starts_at=new_start,
        deposit_percent=settings.deposit_percent,
        deposits_enabled=settings.deposits_enabled,
        service_type=lead.structured_inputs.get("cleaning_type") if lead and lead.structured_inputs else None,
    )

    new_booking = await booking_service.create_booking(
        starts_at=new_start,
        duration_minutes=original.duration_minutes,
        lead_id=original.lead_id,
        session=session,
        deposit_decision=deposit_decision,
        policy_snapshot=deposit_decision.policy_snapshot,
        client_id=identity.client_id,
        lead=lead,
        service_type=lead.structured_inputs.get("cleaning_type") if lead and lead.structured_inputs else None,
    )
    logger.info(
        "client_portal_repeat",
        extra={"extra": {"client_id": identity.client_id, "source_order": order_id, "new_order": new_booking.booking_id}},
    )
    return client_schemas.ClientOrderSummary(
        order_id=new_booking.booking_id,
        status=new_booking.status,
        starts_at=new_booking.starts_at,
        duration_minutes=new_booking.duration_minutes,
    )


@router.post("/client/orders/{order_id}/review")
async def submit_review(
    order_id: str,
    payload: client_schemas.ReviewRequest,
    identity: client_schemas.ClientIdentity = Depends(require_identity),
    session: AsyncSession = Depends(get_db_session),
) -> JSONResponse:
    await _get_client_booking(session, order_id, identity.client_id)
    logger.info(
        "client_review_submitted",
        extra={
            "extra": {
                "client_id": identity.client_id,
                "order_id": order_id,
                "rating": payload.rating,
                "comment": payload.comment,
            }
        },
    )
    return JSONResponse({"status": "received"})


@router.get(
    "/v1/client/slots", response_model=booking_schemas.ClientSlotAvailabilityResponse
)
async def client_slots(
    team_id: int | None = Query(None, ge=1),
    start: datetime = Query(..., alias="from"),
    end: datetime = Query(..., alias="to"),
    duration_minutes: int = Query(booking_schemas.DEFAULT_SLOT_DURATION_MINUTES, gt=0),
    session: AsyncSession = Depends(get_db_session),
) -> booking_schemas.ClientSlotAvailabilityResponse:
    query = booking_schemas.ClientSlotQuery(
        team_id=team_id, start=start, end=end, duration_minutes=duration_minutes
    )
    slots = await booking_service.list_available_slots(
        session,
        query.start,
        query.end,
        query.duration_minutes,
        team_id=query.team_id,
    )
    return booking_schemas.ClientSlotAvailabilityResponse(
        duration_minutes=query.duration_minutes, slots=slots
    )


@router.post(
    "/v1/client/bookings",
    response_model=booking_schemas.ClientBookingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def client_create_booking(
    payload: booking_schemas.ClientBookingRequest,
    identity: client_schemas.ClientIdentity = Depends(require_identity),
    session: AsyncSession = Depends(get_db_session),
) -> booking_schemas.ClientBookingResponse:
    normalized_start = payload.normalized_start()
    lead = None
    if payload.lead_id:
        lead = await session.get(Lead, payload.lead_id)
        if not lead or not lead.email or lead.email.lower() != identity.email.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    try:
        booking = await booking_service.create_booking(
            starts_at=normalized_start,
            duration_minutes=payload.duration_minutes,
            lead_id=payload.lead_id,
            session=session,
            client_id=identity.client_id,
            lead=lead,
            service_type=payload.service_type,
            team_id=payload.team_id,
        )
    except ValueError as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await session.refresh(booking)
    return _client_booking_response(booking)


@router.post(
    "/v1/client/bookings/{booking_id}/reschedule",
    response_model=booking_schemas.ClientBookingResponse,
)
async def client_reschedule_booking(
    booking_id: str,
    payload: booking_schemas.ClientRescheduleRequest,
    identity: client_schemas.ClientIdentity = Depends(require_identity),
    session: AsyncSession = Depends(get_db_session),
) -> booking_schemas.ClientBookingResponse:
    booking = await _get_owned_booking(session, booking_id, identity)

    try:
        booking = await booking_service.reschedule_booking(
            session,
            booking,
            payload.normalized_start(),
            payload.duration_minutes,
        )
    except ValueError as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return _client_booking_response(booking)


@router.post(
    "/v1/client/bookings/{booking_id}/cancel",
    response_model=booking_schemas.ClientBookingResponse,
)
async def client_cancel_booking(
    booking_id: str,
    identity: client_schemas.ClientIdentity = Depends(require_identity),
    session: AsyncSession = Depends(get_db_session),
) -> booking_schemas.ClientBookingResponse:
    booking = await _get_owned_booking(session, booking_id, identity)

    try:
        booking = await booking_service.cancel_booking(session, booking)
    except ValueError as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return _client_booking_response(booking)

