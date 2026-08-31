"""Testowa galeria niefunkcjonalnych prototypów interfejsu KP Orbit."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.core.config import settings

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["orbit-ui"])

ORBIT_MODULES = (
    {
        "name": "Pulpit",
        "path": "/choice",
        "mark": "PU",
        "group": "start",
        "description": "Punkt startowy i katalog dostępnych funkcji.",
    },
    {
        "name": "Telefonia i SMS",
        "path": "/operator",
        "mark": "TS",
        "group": "customer",
        "description": "Połączenia, kontakty i wiadomości do klientów.",
    },
    {
        "name": "Formularze",
        "path": "/genform",
        "mark": "FO",
        "group": "customer",
        "description": "Generowanie i kontrola formularzy klienta.",
    },
    {
        "name": "Asystent",
        "path": "/assistant",
        "mark": "AI",
        "group": "customer",
        "description": "Wsparcie w analizie danych i przygotowaniu raportów.",
    },
    {
        "name": "FLOW",
        "path": "/flow",
        "mark": "FL",
        "group": "process",
        "description": "Obsługa spraw, proform, zgód i realizacji umów.",
    },
    {
        "name": "Dostawy",
        "path": "/contracts",
        "mark": "DO",
        "group": "process",
        "description": "Potwierdzenia danych oraz przygotowanie dostawy.",
    },
    {
        "name": "Urządzenia",
        "path": "/device",
        "mark": "UR",
        "group": "logistics",
        "description": "Przyjęcia, magazyn, historia i audyt urządzeń.",
    },
    {
        "name": "Przesyłki",
        "path": "/shipping",
        "mark": "PR",
        "group": "logistics",
        "description": "Przygotowanie części, etykiet i wysyłek DPD.",
    },
    {
        "name": "Administracja",
        "path": "/admin",
        "mark": "AD",
        "group": "system",
        "description": "Użytkownicy, konfiguracja i kontrola systemu.",
    },
)

ORBIT_MODULE_GROUPS = (
    {"key": "start", "label": "Start", "description": "Najważniejszy punkt wejścia"},
    {
        "key": "customer",
        "label": "Obsługa klienta",
        "description": "Kontakt, formularze i wsparcie",
    },
    {
        "key": "process",
        "label": "Procesy handlowe",
        "description": "Umowy, zgody i dostawy",
    },
    {
        "key": "logistics",
        "label": "Logistyka i majątek",
        "description": "Urządzenia, części i przesyłki",
    },
    {"key": "system", "label": "System", "description": "Administracja i konfiguracja"},
)


@router.get("/orbit/prototypes", response_class=HTMLResponse)
async def orbit_prototypes_page(request: Request) -> HTMLResponse:
    """Renderuje pięć statycznych wariantów pulpitu wyłącznie w profilu testowym."""
    if not settings.is_test_runtime:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return templates.TemplateResponse(
        request,
        "orbit/prototypes.html",
        {"modules": ORBIT_MODULES},
    )


@router.get("/orbit/simple-prototypes", response_class=HTMLResponse)
async def orbit_simple_prototypes_page(request: Request) -> HTMLResponse:
    """Renderuje makiety prostego rebrandingu i rozwijalnego katalogu modułów."""
    if not settings.is_test_runtime:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return templates.TemplateResponse(
        request,
        "orbit/simple_prototypes.html",
        {"modules": ORBIT_MODULES, "module_groups": ORBIT_MODULE_GROUPS},
    )


__all__ = ["router"]
