from datetime import date, datetime
from typing import List

from pydantic import BaseModel, Field, field_validator

from app.domain.invoices import statuses


class InvoiceItemCreate(BaseModel):
    description: str = Field(min_length=1, max_length=255)
    qty: int = Field(gt=0)
    unit_price_cents: int = Field(ge=0)
    tax_rate: float | None = Field(default=None, ge=0)


class InvoiceCreateRequest(BaseModel):
    issue_date: date | None = None
    due_date: date | None = None
    currency: str = Field(default="CAD", max_length=8)
    notes: str | None = Field(default=None, max_length=1000)
    items: List[InvoiceItemCreate] = Field(min_length=1, max_length=50)


class InvoiceItemResponse(BaseModel):
    item_id: int
    description: str
    qty: int
    unit_price_cents: int
    line_total_cents: int
    tax_rate: float | None = None


class PaymentResponse(BaseModel):
    payment_id: str
    provider: str
    method: str
    amount_cents: int
    currency: str
    status: str
    received_at: datetime | None = None
    reference: str | None = None
    created_at: datetime


class InvoiceResponse(BaseModel):
    invoice_id: str
    invoice_number: str
    order_id: str | None
    customer_id: str | None
    status: str
    issue_date: date
    due_date: date | None
    currency: str
    subtotal_cents: int
    tax_cents: int
    total_cents: int
    paid_cents: int
    balance_due_cents: int
    notes: str | None = None
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    items: list[InvoiceItemResponse]
    payments: list[PaymentResponse]


class InvoiceListItem(BaseModel):
    invoice_id: str
    invoice_number: str
    order_id: str | None
    customer_id: str | None
    status: str
    issue_date: date
    due_date: date | None
    currency: str
    subtotal_cents: int
    tax_cents: int
    total_cents: int
    paid_cents: int
    balance_due_cents: int
    created_at: datetime
    updated_at: datetime


class InvoiceListResponse(BaseModel):
    invoices: list[InvoiceListItem]
    page: int
    page_size: int
    total: int


class ManualPaymentRequest(BaseModel):
    amount_cents: int = Field(gt=0)
    method: str = Field(pattern="^(cash|etransfer|other)$")
    reference: str | None = Field(default=None, max_length=255)
    received_at: datetime | None = None

    @field_validator("method")
    @classmethod
    def normalize_method(cls, value: str) -> str:
        return value.lower()


class InvoiceStatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def normalize_status(cls, value: str) -> str:
        return statuses.normalize_status(value)


class ManualPaymentResult(BaseModel):
    invoice: InvoiceResponse
    payment: PaymentResponse
