"""Timeline endpoints for unified audit/event views."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_auth import require_viewer, AdminIdentity
from app.api.org_context import require_org_context
from app.dependencies import get_db_session
from app.domain.timeline import service as timeline_service
from app.domain.timeline.schemas import TimelineResponse

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_viewer)])


@router.get("/v1/admin/timeline/booking/{booking_id}", response_model=TimelineResponse)
async def get_booking_timeline(
    booking_id: str,
    session: AsyncSession = Depends(get_db_session),
    org_id: uuid.UUID = Depends(require_org_context),
    identity: AdminIdentity = Depends(require_viewer),
):
    """Get unified timeline for a booking.

    Returns chronological list of all events related to the booking:
    - Admin audit logs
    - Email events
    - Payment events
    - Photo reviews
    - NPS responses
    - Support tickets
    - Outbox delivery events

    Events include before/after diffs for critical state changes.
    PII (email addresses, metadata) is masked for viewer role.
    """
    from app.shared.pii_masking import should_mask_pii, mask_email, truncate_sensitive_text

    events = await timeline_service.get_booking_timeline(session, org_id, booking_id)

    # Mask PII for viewer role
    if should_mask_pii(identity.role.value):
        for event in events:
            # Mask email addresses in metadata
            if event.metadata:
                if "recipient" in event.metadata:
                    event.metadata["recipient"] = mask_email(event.metadata.get("recipient"))
                if "subject" in event.metadata:
                    event.metadata["subject"] = truncate_sensitive_text(event.metadata.get("subject"), 30)
                if "body" in event.metadata:
                    event.metadata["body"] = truncate_sensitive_text(event.metadata.get("body"), 50)
                if "comment" in event.metadata:
                    event.metadata["comment"] = truncate_sensitive_text(event.metadata.get("comment"), 50)
                if "review_comment" in event.metadata:
                    event.metadata["review_comment"] = truncate_sensitive_text(event.metadata.get("review_comment"), 50)

    return TimelineResponse(
        resource_type="booking",
        resource_id=booking_id,
        events=events,
        total=len(events),
    )


@router.get("/v1/admin/timeline/invoice/{invoice_id}", response_model=TimelineResponse)
async def get_invoice_timeline(
    invoice_id: str,
    session: AsyncSession = Depends(get_db_session),
    org_id: uuid.UUID = Depends(require_org_context),
    identity: AdminIdentity = Depends(require_viewer),
):
    """Get unified timeline for an invoice.

    Returns chronological list of all events related to the invoice:
    - Admin audit logs
    - Email events
    - Payment events
    - Outbox delivery events

    Events include before/after diffs for critical state changes.
    PII (email addresses, metadata) is masked for viewer role.
    """
    from app.shared.pii_masking import should_mask_pii, mask_email, truncate_sensitive_text

    events = await timeline_service.get_invoice_timeline(session, org_id, invoice_id)

    # Mask PII for viewer role
    if should_mask_pii(identity.role.value):
        for event in events:
            # Mask email addresses in metadata
            if event.metadata:
                if "recipient" in event.metadata:
                    event.metadata["recipient"] = mask_email(event.metadata.get("recipient"))
                if "subject" in event.metadata:
                    event.metadata["subject"] = truncate_sensitive_text(event.metadata.get("subject"), 30)

    return TimelineResponse(
        resource_type="invoice",
        resource_id=invoice_id,
        events=events,
        total=len(events),
    )
