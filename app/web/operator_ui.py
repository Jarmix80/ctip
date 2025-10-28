"""Widok panelu operatora."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["operator-ui"])


@router.get("/operator", response_class=HTMLResponse)
async def operator_index(request: Request) -> HTMLResponse:
    """Zwraca stronę główną panelu operatora."""
    return templates.TemplateResponse("operator/index.html", {"request": request})


@router.get("/operator/settings", response_class=HTMLResponse)
async def operator_settings(request: Request) -> HTMLResponse:
    """Strona ustawień operatora (prosta wersja)."""
    return templates.TemplateResponse("operator/settings.html", {"request": request})
