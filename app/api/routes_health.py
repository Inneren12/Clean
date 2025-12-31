import logging
import time
from datetime import datetime, timezone
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.domain.ops.db_models import JobHeartbeat

router = APIRouter()
logger = logging.getLogger(__name__)


_HEAD_CACHE: dict[str, Any] = {"timestamp": 0.0, "heads": None, "skip_reason": None, "warning_logged": False}
_HEAD_CACHE_TTL_SECONDS = 60


def _load_expected_heads() -> tuple[list[str] | None, str | None]:
    """Load expected Alembic heads with a short-lived cache.

    Returns a tuple of (heads, skip_reason). When Alembic metadata is unavailable
    (e.g., packaged deployments without migration files), heads is None and
    skip_reason is populated so callers can treat migrations as skipped.
    """

    now = time.monotonic()
    if now - _HEAD_CACHE["timestamp"] < _HEAD_CACHE_TTL_SECONDS:
        return _HEAD_CACHE["heads"], _HEAD_CACHE["skip_reason"]

    try:
        cfg = Config("alembic.ini")
        cfg.set_main_option("script_location", "alembic")
        script = ScriptDirectory.from_config(cfg)
        heads = script.get_heads()
        _HEAD_CACHE.update({"timestamp": now, "heads": heads, "skip_reason": None})
        return heads, None
    except Exception as exc:  # noqa: BLE001
        skip_reason = "skipped_no_alembic_files"
        if not _HEAD_CACHE["warning_logged"]:
            logger.warning(
                "migrations_check_skipped_no_alembic_files",
                extra={
                    "extra": {
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                        "reason": "alembic config or script directory missing",
                    }
                },
            )
            _HEAD_CACHE["warning_logged"] = True
        _HEAD_CACHE.update({"timestamp": now, "heads": None, "skip_reason": skip_reason})
        return None, skip_reason


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


async def _get_current_revision(session) -> str | None:
    try:
        result = await session.execute(text("SELECT version_num FROM alembic_version"))
    except SQLAlchemyError:
        return None

    row = result.first()
    return row[0] if row else None


async def _database_status(request: Request) -> dict[str, Any]:
    session_factory = getattr(request.app.state, "db_session_factory", None)
    expected_heads, skip_reason = _load_expected_heads()
    expected_head = expected_heads[0] if expected_heads and len(expected_heads) == 1 else None
    expected_heads = expected_heads or []

    if session_factory is None:
        return {
            "ok": False,
            "message": "database session factory unavailable",
            "hint": "app.state.db_session_factory is not configured; ensure startup wiring is complete.",
            "migrations_current": False,
            "current_version": None,
            "expected_head": expected_head,
            "expected_heads": expected_heads,
            "migrations_check": skip_reason or "not_run",
        }

    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
            current_version = await _get_current_revision(session)
    except Exception as exc:  # noqa: BLE001
        logger.debug("database_check_failed", exc_info=exc)
        return {
            "ok": False,
            "message": "database check failed",
            "migrations_current": False,
            "current_version": None,
            "expected_head": expected_head,
            "expected_heads": expected_heads,
            "migrations_check": skip_reason or "not_run",
            "error": exc.__class__.__name__,
        }

    migrations_current: bool
    if skip_reason:
        migrations_current = True
    elif not expected_heads:
        migrations_current = False
    elif len(expected_heads) == 1:
        migrations_current = current_version == expected_head
    else:
        migrations_current = current_version in expected_heads

    return {
        "ok": True,
        "message": "database reachable",
        "migrations_current": migrations_current,
        "current_version": current_version,
        "expected_head": expected_head,
        "expected_heads": expected_heads,
        "migrations_check": skip_reason or "ok",
    }


async def _jobs_status(request: Request) -> dict[str, Any]:
    app_settings = getattr(request.app.state, "app_settings", None)
    heartbeat_required = bool(getattr(app_settings, "job_heartbeat_required", False)) if app_settings else False
    if not heartbeat_required:
        return {"ok": True, "enabled": False, "message": "job heartbeat check disabled"}

    session_factory = getattr(request.app.state, "db_session_factory", None)
    if session_factory is None:
        return {
            "ok": False,
            "enabled": True,
            "message": "database session factory unavailable",
        }

    ttl_seconds = int(getattr(app_settings, "job_heartbeat_ttl_seconds", 300)) if app_settings else 300
    try:
        async with session_factory() as session:
            record = await session.get(JobHeartbeat, "jobs-runner")
    except Exception as exc:  # noqa: BLE001
        logger.debug("jobs_check_failed", exc_info=exc)
        return {
            "ok": False,
            "enabled": True,
            "message": "job heartbeat check failed",
            "error": type(exc).__name__,
        }

    if record is None:
        return {
            "ok": False,
            "enabled": True,
            "message": "job heartbeat missing",
            "threshold_seconds": ttl_seconds,
        }

    last_seen = record.last_heartbeat
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)

    age_seconds = (datetime.now(tz=timezone.utc) - last_seen).total_seconds()
    return {
        "ok": age_seconds <= ttl_seconds,
        "enabled": True,
        "last_heartbeat": last_seen.isoformat(),
        "age_seconds": age_seconds,
        "threshold_seconds": ttl_seconds,
    }


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    database = await _database_status(request)
    jobs = await _jobs_status(request)

    overall_ok = bool(database.get("ok")) and bool(database.get("migrations_current")) and bool(jobs.get("ok"))
    status_code = 200 if overall_ok else 503

    payload = {
        "status": "ok" if overall_ok else "unhealthy",
        "database": database,
        "jobs": jobs,
    }
    return JSONResponse(status_code=status_code, content=payload)
