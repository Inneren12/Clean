from datetime import datetime

from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr


class ClientIdentity(BaseModel):
    client_id: str
    email: EmailStr
    issued_at: datetime


class ClientOrderSummary(BaseModel):
    order_id: str
    status: str
    starts_at: datetime
    duration_minutes: int


class ClientOrderDetail(ClientOrderSummary):
    deposit_required: bool
    deposit_status: str | None = None
    pay_link: str | None = None
    photos_available: bool
    photos_count: int


class ClientInvoiceResponse(BaseModel):
    invoice_id: str
    invoice_number: str
    status: str
    total_cents: int
    currency: str
    issued_at: datetime
    order_id: str | None = None


class ClientInvoiceListItem(BaseModel):
    invoice_id: str
    invoice_number: str
    status: str
    total_cents: int
    currency: str
    issued_at: datetime
    balance_due_cents: int
    order_id: str | None = None


class ReviewRequest(BaseModel):
    rating: int
    comment: str | None = None

