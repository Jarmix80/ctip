"""Strona startowa systemu CTIP."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["root-ui"])


@router.get("/", response_class=HTMLResponse)
async def landing_page(request: Request) -> HTMLResponse:
    """Ekran logowania centralnego."""
    return templates.TemplateResponse("root/index.html", {"request": request})


@router.get("/choice", response_class=HTMLResponse)
async def choice_page(request: Request) -> HTMLResponse:
    """Ekran wyboru sekcji po poprawnym logowaniu."""
    return templates.TemplateResponse("root/choice.html", {"request": request})
