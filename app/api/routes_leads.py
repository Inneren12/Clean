import uuid
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class LeadRequest(BaseModel):
    session_id: str
    contact_name: str
    email: str
    phone: str
    notes: str | None = None


class LeadResponse(BaseModel):
    lead_id: str
    session_id: str
    status: str


@router.post("/v1/leads", response_model=LeadResponse)
async def create_lead(request: LeadRequest) -> LeadResponse:
    return LeadResponse(lead_id=str(uuid.uuid4()), session_id=request.session_id, status="received")
