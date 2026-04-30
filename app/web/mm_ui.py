"""Interfejs webowy raportu MM."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["mm-ui"])


@router.get("/mm", response_class=HTMLResponse)
async def mm_page(request: Request) -> HTMLResponse:
    """Ekran raportu MM z filtrami."""
    return templates.TemplateResponse(request, "mm/index.html")


__all__ = ["router"]
