"""Interfejs webowy modułu wysyłek części."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")
router = APIRouter(tags=["shipping-ui"])


@router.get("/shipping", response_class=HTMLResponse)
async def shipping_page(request: Request) -> HTMLResponse:
    """Renderuje kolejkę i formularz realizacji wysyłki."""
    return templates.TemplateResponse(request, "shipping/index.html")


__all__ = ["router"]
