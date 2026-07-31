"""Interfejs webowy dedykowanej strony obsługi urządzeń."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.core.config import settings

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["device-ui"])
DEVICE_UI_VERSION = "0.6.1"


async def _device_template(request: Request, view: str) -> HTMLResponse:
    """Renderuje wspólny szkielet modułu z wybranym ekranem startowym."""
    return templates.TemplateResponse(
        "device/index.html",
        {
            "request": request,
            "initial_view": view,
            "show_intake_prototypes": settings.is_test_runtime,
            "device_ui_version": DEVICE_UI_VERSION,
        },
    )


@router.get("/device", response_class=HTMLResponse)
async def device_page(request: Request) -> HTMLResponse:
    """Ekran startowy modułu urządzeń."""
    return await _device_template(request, "home")


@router.get("/device/intake", response_class=HTMLResponse)
async def device_intake_page(request: Request) -> HTMLResponse:
    """Formularz przyjęcia urządzeń dokumentem PZ."""
    return await _device_template(request, "intake")


@router.get("/device/bnp-buyout", response_class=HTMLResponse)
async def device_bnp_buyout_page(request: Request) -> HTMLResponse:
    """Formularz wykupu urządzenia po zakończeniu wynajmu BNP."""
    return await _device_template(request, "bnp-buyout")


@router.get("/device/bnp-buyout/prototypes", response_class=HTMLResponse)
async def device_bnp_buyout_prototypes_page(request: Request) -> HTMLResponse:
    """Galeria testowych makiet formularza wykupu BNP."""
    if not settings.is_test_runtime:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return templates.TemplateResponse(
        "device/bnp_buyout_prototypes.html",
        {"request": request},
    )


@router.get("/device/intake/prototypes", response_class=HTMLResponse)
async def device_intake_prototypes_page(request: Request) -> HTMLResponse:
    """Galeria testowych makiet nowego formularza przyjęcia PZ."""
    if not settings.is_test_runtime:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return templates.TemplateResponse(
        "device/intake_prototypes.html",
        {"request": request},
    )


@router.get("/device/style-prototypes", response_class=HTMLResponse)
async def device_style_prototypes_page(request: Request) -> HTMLResponse:
    """Galeria testowych wariantów tła modułu urządzeń."""
    if not settings.is_test_runtime:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return templates.TemplateResponse(
        "device/style_prototypes.html",
        {"request": request},
    )


@router.get("/device/sidebar-prototypes", response_class=HTMLResponse)
async def device_sidebar_prototypes_page(request: Request) -> HTMLResponse:
    """Galeria testowych wariantów lewego menu modułu urządzeń."""
    if not settings.is_test_runtime:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return templates.TemplateResponse(
        "device/sidebar_prototypes.html",
        {"request": request},
    )


@router.get("/device/header-prototypes", response_class=HTMLResponse)
async def device_header_prototypes_page(request: Request) -> HTMLResponse:
    """Galeria testowych wariantów górnego panelu modułu urządzeń."""
    if not settings.is_test_runtime:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return templates.TemplateResponse(
        "device/header_prototypes.html",
        {"request": request},
    )


@router.get("/device/warehouse", response_class=HTMLResponse)
async def device_warehouse_page(request: Request) -> HTMLResponse:
    """Scalony stan magazynu urządzeń."""
    return await _device_template(request, "warehouse")


@router.get("/device/history", response_class=HTMLResponse)
async def device_history_page(request: Request) -> HTMLResponse:
    """Historia idempotentnych operacji przyjęcia."""
    return await _device_template(request, "history")


@router.get("/device/audit", response_class=HTMLResponse)
async def device_audit_page(request: Request) -> HTMLResponse:
    """Audyt spójności urządzeń i kartoteki modeli."""
    return await _device_template(request, "audit")


@router.get("/device/issues", response_class=HTMLResponse)
async def device_issues_page(request: Request) -> HTMLResponse:
    """Lista błędów wymagających uzgodnienia."""
    return await _device_template(request, "issues")


__all__ = ["router"]
