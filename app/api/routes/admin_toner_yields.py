"""Chronione API wydajności tonerów, niezależne od realizacji wysyłek i dokumentów."""

import asyncio
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.api.routes.admin_shipping import _require_shipping_access
from app.core.config import settings
from app.schemas.toner_yield import TonerYieldUpdate
from app.services.toner_yield_source import load_toner_catalog
from app.services.toner_yields import (
    YieldConflict,
    change_yield,
    list_yields,
    refresh_catalog,
    yield_detail,
)

router = APIRouter(prefix="/admin/shipping/toner-yields", tags=["wydajności tonerów"])
DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]
AdminContext = Annotated[tuple, Depends(get_admin_session_context)]
logger = logging.getLogger(__name__)


def can_edit(user):
    """Rozdziela prawo odczytu Shipping od jawnie nadanego prawa korekty wydajności."""
    return user.role == "admin" or bool(user.can_edit_toner_yields)


async def require_editor(context, session):
    """Egzekwuje uprawnienie po stronie API, niezależnie od ukrytych przycisków."""
    user = await _require_shipping_access(context, session)
    if not can_edit(user):
        raise HTTPException(
            status_code=403, detail="Konto nie ma uprawnienia do edycji wydajności tonerów."
        )
    return user


async def scoped_detail(session, item_id):
    """Nie udostępnia kartotek spoza magazynu skonfigurowanego dla Shipping."""
    try:
        item = await yield_detail(session, item_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if item["warehouse_id"] != settings.shipping_warehouse_id:
        raise HTTPException(status_code=404, detail="Kartoteka nie należy do magazynu Shipping.")
    return item


@router.get("")
async def catalog(
    session: DatabaseSession,
    context: AdminContext,
    query: str = Query(default="", max_length=200),
    scope: Literal["active", "inactive", "review", "all"] = "active",
    color: Literal["", "black", "cyan", "magenta", "yellow", "unknown"] = "",
    brand: str = Query(default="", max_length=200),
    supplier: str = Query(default="", max_length=200),
    kind: Literal["", "original", "compatible", "remanufactured", "unknown"] = "",
    status: Literal["", "confirmed", "estimated", "missing"] = "",
    only_available: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
):
    """Zwraca przeszukiwalną stronę katalogu, filtry, liczniki i aktualność odczytu."""
    user = await _require_shipping_access(context, session)
    result = await list_yields(
        session,
        warehouse_id=settings.shipping_warehouse_id,
        query=query,
        scope=scope,
        color=color,
        brand=brand,
        supplier=supplier,
        kind=kind,
        status=status,
        only_available=only_available,
        page=page,
        page_size=page_size,
    )
    return {**result, "can_edit": can_edit(user)}


@router.post("/refresh")
async def refresh(session: DatabaseSession, context: AdminContext):
    """Odświeża kompletny odczyt MS; awaria pozostawia poprzedni katalog bez zmian."""
    await require_editor(context, session)
    try:
        snapshot = await asyncio.to_thread(load_toner_catalog, settings.shipping_warehouse_id)
        result = await refresh_catalog(session, snapshot)
        await session.commit()
    except Exception as error:
        await session.rollback()
        logger.warning("Odświeżanie wydajności tonerów nieudane: %s", type(error).__name__)
        raise HTTPException(
            status_code=503,
            detail="Nie udało się odświeżyć danych z MS. Zachowano poprzedni odczyt i wszystkie wydajności.",
        ) from error
    return result


@router.get("/{item_id}")
async def detail(item_id: int, session: DatabaseSession, context: AdminContext):
    """Udostępnia modele, źródła i historię, nie wykonując odczytu dokumentów w Optimie."""
    user = await _require_shipping_access(context, session)
    return {**await scoped_detail(session, item_id), "can_edit": can_edit(user)}


@router.get("/{item_id}/history")
async def history(item_id: int, session: DatabaseSession, context: AdminContext):
    """Udostępnia pełną historię korekt kartoteki uprawnionemu użytkownikowi."""
    await _require_shipping_access(context, session)
    return {"items": (await scoped_detail(session, item_id))["history"]}


@router.patch("/{item_id}")
async def correct(
    item_id: int, payload: TonerYieldUpdate, session: DatabaseSession, context: AdminContext
):
    """Zatwierdza wartość i audyt razem; konflikt wersji zwraca HTTP 409."""
    user = await require_editor(context, session)
    await scoped_detail(session, item_id)
    try:
        result = await change_yield(
            session, item_id, payload, actor_id=user.id, actor_label=user.email
        )
        await session.commit()
    except YieldConflict as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return result
