import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db_session
from app.domain.bookings.db_models import Booking, OrderPhoto
from app.domain.bookings import service as booking_service
from app.domain.clients import schemas as client_schemas
from app.domain.clients import service as client_service
from app.domain.invoices.db_models import Invoice
from app.domain.leads.db_models import Lead
from app.settings import settings

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
    return await _get_identity_from_token(token)


def _magic_link_destination(request: Request) -> str:
    base_url = settings.client_portal_base_url or settings.public_base_url
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
async def login_callback(token: str) -> HTMLResponse:
    identity = await _get_identity_from_token(token)
    response = HTMLResponse(
        "<html><body><p>Login successful. Continue to <a href='/client'>your dashboard</a>.</p></body></html>"
    )
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        secure=False,
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
    new_booking = await booking_service.create_booking(
        starts_at=new_start,
        duration_minutes=original.duration_minutes,
        lead_id=original.lead_id,
        session=session,
        deposit_decision=None,
        client_id=identity.client_id,
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

