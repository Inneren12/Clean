from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.export_events.db_models import ExportEvent
from app.domain.invoices import service as invoice_service
from app.domain.outbox import service as outbox_service
from app.settings import settings


logger = logging.getLogger(__name__)


def _previous_month_range(today: date | None = None) -> tuple[date, date]:
    now = today or date.today()
    first_of_month = date(year=now.year, month=now.month, day=1)
    last_of_previous_month = first_of_month - timedelta(days=1)
    start = date(year=last_of_previous_month.year, month=last_of_previous_month.month, day=1)
    return start, last_of_previous_month


async def run_accounting_export(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    range_start: date | None = None,
    range_end: date | None = None,
    export_mode: str | None = None,
) -> dict[str, object]:
    start, end = (range_start, range_end)
    if start is None or end is None:
        start, end = _previous_month_range()

    rows = await invoice_service.accounting_export_rows(
        session,
        org_id,
        start=start,
        end=end,
        statuses_filter=invoice_service.DEFAULT_ACCOUNTING_STATUSES,
    )
    csv_content = invoice_service.build_accounting_export_csv(rows)
    payload = {
        "kind": "accounting_export_v1",
        "org_id": str(org_id),
        "range_start": start.isoformat(),
        "range_end": end.isoformat(),
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "row_count": len(rows),
        "csv": csv_content,
    }

    mode = export_mode or settings.export_mode
    target_url = settings.export_webhook_url
    if mode == "webhook" and target_url:
        dedupe_key = f"accounting-export:{org_id}:{start}:{end}"
        await outbox_service.enqueue_outbox_event(
            session,
            org_id=org_id,
            kind="export",
            payload={"payload": payload, "target_url": target_url},
            dedupe_key=dedupe_key,
        )
        await session.commit()
        logger.info(
            "accounting_export_enqueued",
            extra={"extra": {"org_id": str(org_id), "range_start": start.isoformat(), "range_end": end.isoformat()}},
        )
        return {"queued": 1, "stored": 0, "range_start": start, "range_end": end}

    event = ExportEvent(
        mode="accounting_csv",
        payload=payload,
        target_url=None,
        target_url_host=None,
        attempts=0,
        last_error_code=None,
        org_id=org_id,
    )
    session.add(event)
    await session.commit()
    logger.info(
        "accounting_export_stored",
        extra={"extra": {"org_id": str(org_id), "range_start": start.isoformat(), "range_end": end.isoformat()}},
    )
    return {"queued": 0, "stored": 1, "range_start": start, "range_end": end}

