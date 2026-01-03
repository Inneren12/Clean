import argparse
import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.infra.db import get_session_factory
from app.infra.email import EmailAdapter, resolve_email_adapter
from app.infra.metrics import configure_metrics, metrics
from app.jobs.heartbeat import record_heartbeat
from app.jobs import email_jobs, outbox, storage_janitor
from app.infra.storage import new_storage_backend
from app.domain.ops.db_models import JobHeartbeat
from app.settings import settings

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

_ADAPTER: EmailAdapter | None = None
_STORAGE: object | None = None


async def _run_job(
    name: str,
    session_factory: async_sessionmaker,
    runner: Callable[[object], Awaitable[dict[str, int]]],
) -> None:
    async with session_factory() as session:
        result = await runner(session)
    logger.info("job_complete", extra={"extra": {"job": name, **result}})
    _record_email_job_metrics(name, result)
    await _record_job_result(session_factory, name, success=True)


def _record_email_job_metrics(job: str, result: dict[str, int]) -> None:
    sent_total = result.get("sent", 0) + result.get("overdue", 0)
    skipped_total = result.get("skipped", 0)
    if sent_total:
        metrics.record_email_job(job, "sent", sent_total)
    if skipped_total:
        metrics.record_email_job(job, "skipped", skipped_total)


async def _record_job_result(
    session_factory: async_sessionmaker, job: str, *, success: bool, error_reason: str | None = None
) -> None:
    now = datetime.now(tz=timezone.utc)
    async with session_factory() as session:
        record = await session.get(JobHeartbeat, job)
        if record is None:
            record = JobHeartbeat(
                name=job,
                last_heartbeat=now,
                last_success_at=now if success else None,
                consecutive_failures=0,
                last_error=None,
                last_error_at=None,
                updated_at=now,
            )
            session.add(record)
        else:
            record.last_heartbeat = now
            if success:
                record.last_success_at = now
                record.consecutive_failures = 0
                record.last_error = None
                record.last_error_at = None
            else:
                record.consecutive_failures = (record.consecutive_failures or 0) + 1
                record.last_error = error_reason or record.last_error
                record.last_error_at = now
        await session.commit()
    if success:
        metrics.record_job_success(job, now.timestamp())
    else:
        metrics.record_job_error(job, error_reason or "unknown")


def _job_runner(name: str, base_url: str | None = None) -> Callable:
    if name == "booking-reminders":
        return lambda session: email_jobs.run_booking_reminders(session, _ADAPTER)
    if name == "invoice-reminders":
        return lambda session: email_jobs.run_invoice_notifications(session, _ADAPTER, base_url=base_url)
    if name == "nps-send":
        return lambda session: email_jobs.run_nps_sends(session, _ADAPTER, base_url=base_url)
    if name == "email-dlq":
        return lambda session: email_jobs.run_email_dlq(session, _ADAPTER)
    if name == "outbox-delivery":
        return lambda session: outbox.run_outbox_delivery(session, _ADAPTER)
    if name == "storage-janitor":
        return lambda session: storage_janitor.run_storage_janitor(session, _STORAGE)
    raise ValueError(f"unknown_job:{name}")


async def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run scheduled jobs")
    parser.add_argument("--job", action="append", dest="jobs", help="Job name to run")
    parser.add_argument("--interval", type=int, default=60, help="Seconds between loops when not using --once")
    parser.add_argument("--base-url", dest="base_url", default=None, help="Public base URL for links")
    parser.add_argument("--once", action="store_true", help="Run jobs once and exit")
    args = parser.parse_args(argv)

    global _ADAPTER, _STORAGE
    _ADAPTER = resolve_email_adapter(settings)
    _STORAGE = new_storage_backend()
    configure_metrics(settings.metrics_enabled)
    session_factory = get_session_factory()

    job_names = args.jobs or [
        "booking-reminders",
        "invoice-reminders",
        "nps-send",
        "email-dlq",
        "outbox-delivery",
        "storage-janitor",
    ]
    runners = [_job_runner(name, base_url=args.base_url) for name in job_names]

    while True:
        for name, runner in zip(job_names, runners):
            try:
                await _run_job(name, session_factory, runner)
            except Exception as exc:  # noqa: BLE001
                metrics.record_email_job(name, "error")
                logger.warning("job_failed", extra={"extra": {"job": name, "reason": type(exc).__name__}})
                await _record_job_result(
                    session_factory, name, success=False, error_reason=type(exc).__name__
                )
        await record_heartbeat(session_factory, name="jobs-runner")
        if args.once:
            break
        await asyncio.sleep(max(args.interval, 1))


if __name__ == "__main__":
    asyncio.run(main())
