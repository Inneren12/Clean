from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

router = APIRouter()


# Resolve the expected Alembic head revision from migration filenames.
def _resolve_head_revision() -> str | None:
    versions_dir = Path(__file__).resolve().parents[2] / "alembic" / "versions"
    if not versions_dir.exists():
        return None

    revision_files = [path for path in versions_dir.glob("*.py") if path.is_file()]
    if not revision_files:
        return None

    return max(path.stem for path in revision_files)


ALEMBIC_HEAD_REVISION = _resolve_head_revision()


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
    if session_factory is None:
        return {
            "ok": False,
            "message": "database session factory unavailable",
            "migrations_current": False,
            "current_version": None,
            "expected_head": ALEMBIC_HEAD_REVISION,
        }

    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
            current_version = await _get_current_revision(session)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "message": str(exc),
            "migrations_current": False,
            "current_version": None,
            "expected_head": ALEMBIC_HEAD_REVISION,
        }

    migrations_current = (
        current_version is not None
        and ALEMBIC_HEAD_REVISION is not None
        and current_version == ALEMBIC_HEAD_REVISION
    )

    return {
        "ok": True,
        "message": "database reachable",
        "migrations_current": migrations_current,
        "current_version": current_version,
        "expected_head": ALEMBIC_HEAD_REVISION,
    }


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    database = await _database_status(request)

    overall_ok = bool(database.get("ok")) and bool(database.get("migrations_current"))
    status_code = 200 if overall_ok else 503

    payload = {
        "status": "ok" if overall_ok else "unhealthy",
        "database": database,
    }
    return JSONResponse(status_code=status_code, content=payload)
