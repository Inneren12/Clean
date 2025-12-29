from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(include_in_schema=False)
try:
    templates = Jinja2Templates(directory="app/templates")
except AssertionError as exc:
    templates = None
    template_error = exc


@router.get("/style-guide", response_class=HTMLResponse)
async def style_guide(request: Request) -> HTMLResponse:
    if templates is None:
        raise HTTPException(status_code=503, detail=f"Style guide disabled: {template_error}")
    return templates.TemplateResponse("pages/style_guide.html", {"request": request})
