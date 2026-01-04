from datetime import date, datetime
import uuid
from decimal import Decimal
from typing import List

from pydantic import BaseModel, Field, field_validator

from app.domain.invoices import statuses
from app.domain.queues.schemas import QuickActionItem


class InvoiceItemCreate(BaseModel):
    description: str = Field(min_length=1, max_length=255)
    qty: int = Field(gt=0)
    unit_price_cents: int = Field(ge=0)
    tax_rate: Decimal | None = Field(default=None, ge=0)


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
    provider_ref: str | None = None
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
    method: str = Field(pattern="^(cash|etransfer|other|card)$")
    reference: str | None = Field(default=None, max_length=255)
    received_at: datetime | None = None

    @field_validator("method")
    @classmethod
    def normalize_method(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in statuses.PAYMENT_METHODS:
            raise ValueError("Invalid payment method")
        return normalized


class InvoiceStatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def normalize_status(cls, value: str) -> str:
        return statuses.normalize_status(value)


class ManualPaymentResult(BaseModel):
    invoice: InvoiceResponse
    payment: PaymentResponse


class InvoiceSendResponse(BaseModel):
    invoice: InvoiceResponse
    public_link: str
    email_sent: bool


class InvoicePaymentInitResponse(BaseModel):
    provider: str
    amount_cents: int
    currency: str
    checkout_url: str | None = None
    client_secret: str | None = None


class GstReportResponse(BaseModel):
    range_start: date
    range_end: date
    invoice_count: int
    taxable_subtotal_cents: int
    tax_cents: int


class SalesLedgerRow(BaseModel):
    invoice_number: str
    issue_date: date
    due_date: date | None
    status: str
    currency: str
    subtotal_cents: int
    tax_cents: int
    total_cents: int
    paid_cents: int
    balance_due_cents: int
    customer_id: str | None = None
    booking_id: str | None = None


class PaymentLedgerRow(BaseModel):
    payment_id: str
    invoice_number: str | None
    booking_id: str | None
    provider: str
    method: str
    status: str
    amount_cents: int
    currency: str
    received_at: datetime | None = None
    created_at: datetime


class PnlRow(BaseModel):
    booking_id: str
    invoice_number: str
    revenue_cents: int
    labour_cents: int
    payment_fees_cents: int
    margin_cents: int
    margin_pct: float | None
    worker_rate_cents: int
    actual_minutes: int


class PnlReportResponse(BaseModel):
    range_start: date
    range_end: date
    rows: list[PnlRow]


class InvoiceReconcileItem(BaseModel):
    invoice_id: str
    invoice_number: str
    status: str
    total_cents: int
    outstanding_cents: int
    succeeded_payments_count: int
    last_payment_at: datetime | None = None
    quick_actions: list[QuickActionItem] = Field(default_factory=list)


class InvoiceReconcileListResponse(BaseModel):
    items: list[InvoiceReconcileItem]


class StripeEventView(BaseModel):
    event_id: str
    type: str | None = None
    created_at: datetime
    org_id: uuid.UUID | None = None
    invoice_id: str | None = None
    booking_id: str | None = None
    processed_status: str
    last_error: str | None = None


class StripeEventListResponse(BaseModel):
    items: list[StripeEventView]
    total: int
    limit: int
    offset: int
