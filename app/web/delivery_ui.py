"""Interfejs webowy modułu obsługi dostaw."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["delivery-ui"])


@router.get("/delivery", response_class=HTMLResponse)
async def delivery_page(request: Request) -> HTMLResponse:
    """Ekran obsługi dostaw i kalendarza końców umów GRENKE."""
    return templates.TemplateResponse("delivery/index.html", {"request": request})


__all__ = ["router"]
