"""Interfejs webowy dedykowanej strony obslugi urzadzen."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["device-ui"])


@router.get("/device", response_class=HTMLResponse)
async def device_page(request: Request) -> HTMLResponse:
    """Ekran dashboardu procesu urzadzen w module /device."""
    return templates.TemplateResponse("device/index.html", {"request": request})


__all__ = ["router"]
