from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_auth import AdminIdentity, AdminRole
from app.domain.admin_audit import service as audit_service
from app.domain.export_events.db_models import ExportEvent
from app.domain.leads.db_models import Lead
from app.domain.leads.service import export_payload_from_lead
from app.domain.outbox.db_models import OutboxEvent
from app.domain.outbox.service import (
    OutboxAdapters,
    deliver_outbox_event,
    outbox_counts_by_status,
)
from app.infra.export import send_export_with_retry, validate_webhook_url
from app.infra.metrics import metrics
from app.infra.org_context import org_id_context
from app.settings import settings


AUTO_REPLAY_ACTOR = "dlq-auto-replay"


async def _export_payload_for_event(session: AsyncSession, event: ExportEvent, org_id: uuid.UUID) -> dict:
    if event.payload:
        return event.payload
    if not event.lead_id:
        return {}
    stmt = select(Lead).where(Lead.lead_id == event.lead_id, Lead.org_id == org_id)
    result = await session.execute(stmt)
    lead = result.scalar_one_or_none()
    return export_payload_from_lead(lead) if lead else {}


async def run_dlq_auto_replay(
    session: AsyncSession,
    adapter,
    *,
    org_id: uuid.UUID | None = None,
    export_transport=None,
    export_resolver=None,
) -> dict[str, int]:
    if not settings.dlq_auto_replay_enabled:
        return {"skipped": 1, "processed": 0, "sent": 0, "failed": 0}

    allowed_kinds = set(settings.dlq_auto_replay_allow_outbox_kinds)
    allowed_export_modes = set(settings.dlq_auto_replay_allow_export_modes)
    if not allowed_kinds and not allowed_export_modes:
        return {"skipped": 1, "processed": 0, "sent": 0, "failed": 0}

    org_uuid = org_id or settings.default_org_id
    cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=settings.dlq_auto_replay_min_age_minutes)
    adapters = OutboxAdapters(
        email_adapter=adapter, export_transport=export_transport, export_resolver=export_resolver
    )
    identity = AdminIdentity(username=AUTO_REPLAY_ACTOR, role=AdminRole.ADMIN, org_id=org_uuid)

    with org_id_context(org_uuid):
        before_outbox = await outbox_counts_by_status(session, ["dead"])
        export_dead_count = await session.scalar(
            select(func.count())
            .select_from(ExportEvent)
            .where(
                ExportEvent.org_id == org_uuid,
                ExportEvent.last_error_code.is_not(None),
            )
        )
        metrics.record_dlq_depth("outbox_dead", "before", before_outbox.get("dead", 0))
        metrics.record_dlq_depth("export_dead", "before", int(export_dead_count or 0))

        sent = 0
        failed = 0
        processed = 0
        failure_streak = 0
        limit = max(0, settings.dlq_auto_replay_max_per_org)

        outbox_stmt = (
            select(OutboxEvent)
            .where(
                OutboxEvent.org_id == org_uuid,
                OutboxEvent.status == "dead",
                OutboxEvent.kind.in_(allowed_kinds),
                OutboxEvent.created_at <= cutoff,
                OutboxEvent.attempts < settings.dlq_auto_replay_outbox_attempt_ceiling,
            )
            .order_by(OutboxEvent.created_at)
            .limit(limit)
        )
        outbox_events = (await session.execute(outbox_stmt)).scalars().all()

        remaining = max(0, limit - len(outbox_events))

        export_stmt = (
            select(ExportEvent)
            .where(
                ExportEvent.org_id == org_uuid,
                ExportEvent.last_error_code.is_not(None),
                ExportEvent.created_at <= cutoff,
                ExportEvent.mode.in_(allowed_export_modes),
                ExportEvent.replay_count < settings.dlq_auto_replay_export_replay_limit,
                (
                    ExportEvent.last_replayed_at.is_(None)
                    | (
                        ExportEvent.last_replayed_at
                        <= datetime.now(tz=timezone.utc)
                        - timedelta(minutes=settings.dlq_auto_replay_export_cooldown_minutes)
                    )
                ),
            )
            .order_by(ExportEvent.created_at)
            .limit(remaining)
        )
        export_events = (await session.execute(export_stmt)).scalars().all()

        for event in outbox_events:
            if processed >= limit or failure_streak >= settings.dlq_auto_replay_failure_streak_limit:
                break
            before_state = {
                "status": event.status,
                "attempts": event.attempts,
                "last_error": event.last_error,
            }
            delivered, error = await deliver_outbox_event(session, event, adapters)
            metrics.record_dlq_replay("outbox", "success" if delivered else "failure", str(org_uuid))
            await audit_service.record_action(
                session,
                identity=identity,
                action="dlq_auto_replay",
                resource_type="outbox_event",
                resource_id=str(event.event_id),
                before=before_state,
                after={
                    "status": event.status,
                    "attempts": event.attempts,
                    "last_error": event.last_error,
                    "error": error,
                },
            )
            processed += 1
            if delivered:
                sent += 1
                failure_streak = 0
            else:
                failed += 1
                failure_streak += 1
                if failure_streak >= settings.dlq_auto_replay_failure_streak_limit:
                    break

        for event in export_events:
            if processed >= limit or failure_streak >= settings.dlq_auto_replay_failure_streak_limit:
                break
            payload = await _export_payload_for_event(session, event, org_uuid)
            if not payload:
                failed += 1
                metrics.record_dlq_replay("export", "failure", str(org_uuid))
                failure_streak += 1
                continue
            target_url = event.target_url or settings.export_webhook_url
            is_valid, reason = await validate_webhook_url(target_url, resolver=export_resolver)
            if not is_valid:
                failed += 1
                metrics.record_dlq_replay("export", "failure", str(org_uuid))
                failure_streak += 1
                continue
            success, attempts, last_error_code = await send_export_with_retry(
                target_url, payload, transport=export_transport
            )
            now = datetime.now(tz=timezone.utc)
            before_state = {
                "attempts": event.attempts,
                "last_error_code": event.last_error_code,
                "replay_count": event.replay_count,
            }
            event.payload = payload
            event.target_url = target_url
            parsed = urlparse(target_url) if target_url else None
            event.target_url_host = parsed.hostname if parsed else event.target_url_host
            event.attempts = attempts
            event.last_error_code = None if success else last_error_code
            event.replay_count = (event.replay_count or 0) + 1
            event.last_replayed_at = now
            event.last_replayed_by = identity.username
            await audit_service.record_action(
                session,
                identity=identity,
                action="dlq_auto_replay",
                resource_type="export_event",
                resource_id=str(event.event_id),
                before=before_state,
                after={
                    "attempts": event.attempts,
                    "last_error_code": event.last_error_code,
                    "replay_count": event.replay_count,
                    "last_replayed_at": event.last_replayed_at.isoformat() if event.last_replayed_at else None,
                },
            )
            metrics.record_dlq_replay("export", "success" if success else "failure", str(org_uuid))
            processed += 1
            if success:
                sent += 1
                failure_streak = 0
            else:
                failed += 1
                failure_streak += 1
                if failure_streak >= settings.dlq_auto_replay_failure_streak_limit:
                    break

        await session.commit()
        after_outbox = await outbox_counts_by_status(session, ["dead"])
        export_dead_after = await session.scalar(
            select(func.count())
            .select_from(ExportEvent)
            .where(ExportEvent.org_id == org_uuid, ExportEvent.last_error_code.is_not(None))
        )
        metrics.record_dlq_depth("outbox_dead", "after", after_outbox.get("dead", 0))
        metrics.record_dlq_depth("export_dead", "after", int(export_dead_after or 0))

    return {
        "processed": processed,
        "sent": sent,
        "failed": failed,
        "skipped": 0,
    }
