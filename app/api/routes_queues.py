"""Admin queue endpoints for operator productivity."""

import logging
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_auth import require_admin, require_dispatch, require_finance, require_viewer, AdminIdentity
from app.api.org_context import require_org_context
from app.dependencies import get_db_session
from app.domain.queues import service as queue_service
from app.domain.queues.schemas import (
    AssignmentQueueResponse,
    DLQResponse,
    InvoiceQueueResponse,
    PhotoQueueResponse,
)

logger = logging.getLogger(__name__)

# Router without default dependencies - each endpoint specifies its own RBAC
router = APIRouter()


@router.get("/v1/admin/queue/photos", response_model=PhotoQueueResponse)
async def get_photo_queue(
    status: Literal["pending", "needs_retake", "all"] = Query("all"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
    org_id: uuid.UUID = Depends(require_org_context),
    identity: AdminIdentity = Depends(require_dispatch),
):
    """List photos requiring review.

    Requires: DISPATCH permission (dispatcher/admin/owner roles)

    Filters:
    - pending: Photos awaiting initial review
    - needs_retake: Photos marked for retake
    - all: All photos (default)

    Returns paginated list with quick actions for approval/rejection.
    """
    items, total, counts = await queue_service.list_photo_queue(
        session, org_id, status_filter=status, limit=limit, offset=offset
    )

    return PhotoQueueResponse(
        items=items,
        total=total,
        pending_count=counts["pending"],
        needs_retake_count=counts["needs_retake"],
    )


@router.get("/v1/admin/queue/invoices", response_model=InvoiceQueueResponse)
async def get_invoice_queue(
    status: Literal["overdue", "unpaid", "all"] = Query("all"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
    org_id: uuid.UUID = Depends(require_org_context),
    identity: AdminIdentity = Depends(require_finance),
):
    """List invoices requiring attention.

    Requires: FINANCE permission (accountant/finance/admin/owner roles)

    Filters:
    - overdue: Invoices past due date
    - unpaid: All unpaid invoices (draft/sent/overdue)
    - all: All unpaid (default)

    Returns paginated list with quick actions for resend/mark paid.
    PII (customer email) is masked for viewer role if accessed via other endpoints.
    """
    items, total, counts = await queue_service.list_invoice_queue(
        session, org_id, status_filter=status, limit=limit, offset=offset
    )

    return InvoiceQueueResponse(
        items=items,
        total=total,
        overdue_count=counts["overdue"],
        unpaid_count=counts["unpaid"],
    )


@router.get("/v1/admin/queue/assignments", response_model=AssignmentQueueResponse)
async def get_assignment_queue(
    days_ahead: int = Query(7, ge=1, le=30, description="Look ahead window in days"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
    org_id: uuid.UUID = Depends(require_org_context),
    identity: AdminIdentity = Depends(require_dispatch),
):
    """List unassigned bookings in the next N days.

    Requires: DISPATCH permission (dispatcher/admin/owner roles)

    Returns bookings without assigned workers, sorted by start time.
    Includes urgency indicator (within 24h).
    PII (lead email/phone) is masked for viewer role if accessed via other endpoints.
    """
    items, total, counts = await queue_service.list_assignment_queue(
        session, org_id, days_ahead=days_ahead, limit=limit, offset=offset
    )

    return AssignmentQueueResponse(
        items=items,
        total=total,
        urgent_count=counts["urgent"],
    )


@router.get("/v1/admin/queue/dlq", response_model=DLQResponse)
async def get_dlq(
    kind: Literal["outbox", "export", "all"] = Query("all"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
    org_id: uuid.UUID = Depends(require_org_context),
    identity: AdminIdentity = Depends(require_admin),
):
    """List dead letter queue items (failed outbox + export events).

    Requires: ADMIN permission (admin/owner roles only)

    Filters:
    - outbox: Failed email/webhook/export outbox events
    - export: Failed legacy export events
    - all: Both (default)

    Returns paginated list with quick actions for replay.
    Uses SQL-level pagination for scalability.
    """
    items, total, counts = await queue_service.list_dlq(
        session, org_id, kind_filter=kind, limit=limit, offset=offset
    )

    return DLQResponse(
        items=items,
        total=total,
        outbox_dead_count=counts["outbox_dead"],
        export_dead_count=counts["export_dead"],
    )
