from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory="app/templates")


@router.get("/style-guide", response_class=HTMLResponse)
async def style_guide(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("pages/style_guide.html", {"request": request})
