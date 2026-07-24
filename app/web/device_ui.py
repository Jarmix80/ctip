"""Interfejs webowy dedykowanej strony obsługi urządzeń."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["device-ui"])


async def _device_template(request: Request, view: str) -> HTMLResponse:
    """Renderuje wspólny szkielet modułu z wybranym ekranem startowym."""
    return templates.TemplateResponse(
        "device/index.html",
        {"request": request, "initial_view": view},
    )


@router.get("/device", response_class=HTMLResponse)
async def device_page(request: Request) -> HTMLResponse:
    """Ekran startowy modułu urządzeń."""
    return await _device_template(request, "home")


@router.get("/device/intake", response_class=HTMLResponse)
async def device_intake_page(request: Request) -> HTMLResponse:
    """Formularz przyjęcia urządzeń dokumentem PZ."""
    return await _device_template(request, "intake")


@router.get("/device/warehouse", response_class=HTMLResponse)
async def device_warehouse_page(request: Request) -> HTMLResponse:
    """Scalony stan magazynu urządzeń."""
    return await _device_template(request, "warehouse")


@router.get("/device/history", response_class=HTMLResponse)
async def device_history_page(request: Request) -> HTMLResponse:
    """Historia idempotentnych operacji przyjęcia."""
    return await _device_template(request, "history")


@router.get("/device/issues", response_class=HTMLResponse)
async def device_issues_page(request: Request) -> HTMLResponse:
    """Lista błędów wymagających uzgodnienia."""
    return await _device_template(request, "issues")


__all__ = ["router"]
