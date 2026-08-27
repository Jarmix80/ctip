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


@router.get("/shipping/v2", response_class=HTMLResponse)
async def shipping_v2_page(request: Request) -> HTMLResponse:
    """Renderuje alternatywny, funkcjonalny interfejs wysyłek w ciemnym układzie."""
    return templates.TemplateResponse(request, "shipping/v2.html")


@router.get("/shipping/prototypes", response_class=HTMLResponse)
async def shipping_prototypes_page(request: Request) -> HTMLResponse:
    """Renderuje siedem niefunkcjonalnych wariantów interfejsu wysyłek."""
    return templates.TemplateResponse(request, "shipping/prototypes.html")


__all__ = ["router"]
