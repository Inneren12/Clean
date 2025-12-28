from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ChecklistTemplateItemPayload(BaseModel):
    label: str
    phase: Literal["BEFORE", "AFTER"]
    required: bool = False
    position: int | None = None


class ChecklistTemplateRequest(BaseModel):
    name: str
    service_type: str | None = None
    version: int | None = None
    is_active: bool = True
    items: list[ChecklistTemplateItemPayload] = Field(default_factory=list)


class ChecklistTemplateUpdateRequest(BaseModel):
    name: str | None = None
    service_type: str | None = None
    version: int | None = None
    is_active: bool | None = None
    items: list[ChecklistTemplateItemPayload] | None = None


class ChecklistTemplateItemResponse(BaseModel):
    item_id: int
    position: int
    label: str
    phase: str
    required: bool


class ChecklistTemplateResponse(BaseModel):
    template_id: int
    name: str
    service_type: str | None
    version: int
    is_active: bool
    items: list[ChecklistTemplateItemResponse]


class ChecklistInitRequest(BaseModel):
    template_id: int | None = None
    service_type: str | None = None


class ChecklistRunItemPatch(BaseModel):
    checked: bool | None = None
    note: str | None = None


class ChecklistRunItemResponse(BaseModel):
    run_item_id: str
    template_item_id: int
    label: str
    phase: str
    required: bool
    position: int
    checked: bool
    checked_at: datetime | None
    note: str | None


class ChecklistRunResponse(BaseModel):
    run_id: str
    order_id: str
    template_id: int
    template_name: str
    template_version: int
    service_type: str | None
    status: str
    created_at: datetime
    completed_at: datetime | None
    items: list[ChecklistRunItemResponse]
