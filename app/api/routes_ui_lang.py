from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from app.infra.i18n import validate_lang

router = APIRouter()


def _validate_next_path(raw_next: str | None) -> str:
    if not raw_next:
        return "/"
    next_path = raw_next.strip()
    if next_path.startswith(("http://", "https://", "//")):
        raise HTTPException(status_code=400, detail="Invalid redirect target")
    if not next_path.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid redirect target")
    return next_path


@router.get("/ui/lang")
async def set_ui_lang(lang: str = Query(...), next_path: str = Query("/", alias="next")) -> RedirectResponse:
    normalized = validate_lang(lang)
    if not normalized:
        raise HTTPException(status_code=400, detail="Unsupported language")
    target = _validate_next_path(next_path)
    response = RedirectResponse(url=target)
    response.set_cookie(
        "ui_lang",
        normalized,
        httponly=False,
        samesite="lax",
    )
    return response
