"""Interfejs webowy modułu wysyłek części."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.core.config import settings

templates = Jinja2Templates(directory="app/templates")
router = APIRouter(tags=["shipping-ui"])


def _require_shipping_enabled() -> None:
    """Nie renderuje modułu przed jawnym włączeniem produkcyjnym."""
    if not settings.shipping_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Moduł Shipping jest wyłączony w konfiguracji produkcyjnej.",
        )


@router.get("/shipping", response_class=HTMLResponse)
async def shipping_page(request: Request) -> HTMLResponse:
    """Renderuje kolejkę i formularz realizacji wysyłki."""
    _require_shipping_enabled()
    return templates.TemplateResponse(request, "shipping/index.html")


@router.get("/shipping/v2", response_class=HTMLResponse)
async def shipping_v2_page(request: Request) -> HTMLResponse:
    """Renderuje alternatywny, funkcjonalny interfejs wysyłek w ciemnym układzie."""
    _require_shipping_enabled()
    return templates.TemplateResponse(request, "shipping/v2.html")


@router.get("/shipping/prototypes", response_class=HTMLResponse)
async def shipping_prototypes_page(request: Request) -> HTMLResponse:
    """Renderuje siedem niefunkcjonalnych wariantów interfejsu wysyłek."""
    _require_shipping_enabled()
    return templates.TemplateResponse(request, "shipping/prototypes.html")


__all__ = ["router"]
