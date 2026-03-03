"""Interfejs webowy dashboardu obslugi umow."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["contracts-ui"])


@router.get("/contracts", response_class=HTMLResponse)
async def contracts_page(request: Request) -> HTMLResponse:
    """Ekran dashboardu obslugi umow."""
    return templates.TemplateResponse("contracts/index.html", {"request": request})


__all__ = ["router"]
