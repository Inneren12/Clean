import argparse
import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.infra.db import get_session_factory
from app.infra.email import EmailAdapter, resolve_email_adapter
from app.jobs import email_jobs
from app.settings import settings

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

_ADAPTER: EmailAdapter | None = None


async def _run_job(
    name: str,
    session_factory: async_sessionmaker,
    runner: Callable[[object], Awaitable[dict[str, int]]],
) -> None:
    async with session_factory() as session:
        result = await runner(session)
    logger.info("job_complete", extra={"extra": {"job": name, **result}})


def _job_runner(name: str, base_url: str | None = None) -> Callable:
    if name == "booking-reminders":
        return lambda session: email_jobs.run_booking_reminders(session, _ADAPTER)
    if name == "invoice-reminders":
        return lambda session: email_jobs.run_invoice_notifications(session, _ADAPTER, base_url=base_url)
    if name == "nps-send":
        return lambda session: email_jobs.run_nps_sends(session, _ADAPTER, base_url=base_url)
    raise ValueError(f"unknown_job:{name}")


async def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run scheduled jobs")
    parser.add_argument("--job", action="append", dest="jobs", help="Job name to run")
    parser.add_argument("--interval", type=int, default=60, help="Seconds between loops when not using --once")
    parser.add_argument("--base-url", dest="base_url", default=None, help="Public base URL for links")
    parser.add_argument("--once", action="store_true", help="Run jobs once and exit")
    args = parser.parse_args(argv)

    global _ADAPTER
    _ADAPTER = resolve_email_adapter(settings)
    session_factory = get_session_factory()

    job_names = args.jobs or ["booking-reminders", "invoice-reminders", "nps-send"]
    runners = [_job_runner(name, base_url=args.base_url) for name in job_names]

    while True:
        for name, runner in zip(job_names, runners):
            try:
                await _run_job(name, session_factory, runner)
            except Exception as exc:  # noqa: BLE001
                logger.warning("job_failed", extra={"extra": {"job": name, "reason": type(exc).__name__}})
        if args.once:
            break
        await asyncio.sleep(max(args.interval, 1))


if __name__ == "__main__":
    asyncio.run(main())
