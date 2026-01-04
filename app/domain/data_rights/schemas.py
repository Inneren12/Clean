from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, field_validator


class DataExportRequest(BaseModel):
    lead_id: str | None = None
    email: EmailStr | None = None

    @field_validator("email")
    @classmethod
    def at_least_one(cls, value: str | None, values: dict[str, object]) -> str | None:  # noqa: ANN001
        if not value and not values.get("lead_id"):
            raise ValueError("lead_id_or_email_required")
        return value


class DataExportResponse(BaseModel):
    leads: list[dict]
    bookings: list[dict]
    invoices: list[dict]
    payments: list[dict]
    photos: list[dict]


class DataDeletionRequestPayload(BaseModel):
    lead_id: str | None = None
    email: EmailStr | None = None
    reason: str | None = None

    @field_validator("email")
    @classmethod
    def validate_target(cls, value: str | None, values: dict[str, object]) -> str | None:  # noqa: ANN001
        if not value and not values.get("lead_id"):
            raise ValueError("lead_id_or_email_required")
        return value


class DataDeletionResponse(BaseModel):
    request_id: str
    status: str
    matched_leads: int
    pending_deletions: int
    requested_at: datetime


class DataDeletionCleanupResult(BaseModel):
    processed: int
    leads_anonymized: int
    photos_deleted: int
    invoices_detached: int
