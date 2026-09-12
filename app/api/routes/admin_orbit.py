"""Chronione raporty KP ORBIT, odczytowa ocena wysyłki i audytowane ustawienia."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.api.routes.admin_shipping import _require_shipping_access
from app.core.config import settings
from app.services import orbit, orbit_policy
from app.services.audit import record_audit

router = APIRouter(prefix="/admin/shipping/orbit", tags=["KP ORBIT"])
DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]
AdminContext = Annotated[tuple, Depends(get_admin_session_context)]


class PolicyValues(BaseModel):
    """Puste pola dziedziczą wartości nadrzędne; zero zapasu jest jawną regułą."""

    model_config = ConfigDict(extra="forbid")
    spare_toners: StrictInt | None = Field(None, ge=0, le=20)
    low_percent: StrictInt | None = Field(None, ge=0, le=100)
    lead_days: StrictInt | None = Field(None, ge=1, le=90)
    cpc_stale_days: StrictInt | None = Field(None, ge=1, le=365)
    measurement_stale_days: StrictInt | None = Field(None, ge=1, le=90)


class PolicyUpdate(BaseModel):
    """Zakres ustawień i kolor, bez dowolnych kluczy konfiguracyjnych."""

    model_config = ConfigDict(extra="forbid")
    scope: Literal["global", "customer", "device"]
    scope_id: str = Field(min_length=1, max_length=60)
    color: Literal["all", "black", "cyan", "magenta", "yellow"] = "all"
    values: PolicyValues


class DraftItem(BaseModel):
    """Dodatnia ilość kartoteki ocenianej doradczo, nie dyspozycja magazynowa."""

    model_config = ConfigDict(extra="forbid")
    item_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, le=10000, allow_inf_nan=False)


class ShipmentDraft(BaseModel):
    """Szkic pojedynczego zlecenia w zwykłej lub łączonej paczce."""

    model_config = ConfigDict(extra="forbid")
    order_table_id: int = Field(gt=0)
    device_id: str = Field(pattern=r"^ms:[1-9][0-9]*$")
    items: list[DraftItem] = Field(min_length=1, max_length=100)


class AssessmentRequest(BaseModel):
    """Ograniczony zestaw szkiców, bez zmian w bazach źródłowych."""

    model_config = ConfigDict(extra="forbid")
    drafts: list[ShipmentDraft] = Field(min_length=1, max_length=100)


class SavedAssessmentRequest(BaseModel):
    """Numery istniejących zleceń do oceny wspólnej paczki."""

    model_config = ConfigDict(extra="forbid")
    order_table_ids: list[Annotated[int, Field(gt=0)]] = Field(min_length=1, max_length=100)


def can_view_finance(user):
    """Administrator ma dostęp; operator wymaga osobnego jawnego uprawnienia."""
    return user.role == "admin" or bool(getattr(user, "can_view_orbit_finance", False))


async def access(session, context):
    """Sprawdza uprawnienia przed odczytem nowych tabel lub danych finansowych."""
    user = await _require_shipping_access(context, session)
    if not settings.shipping_orbit_enabled:
        raise HTTPException(status_code=404, detail="KP ORBIT jest wyłączony.")
    return can_view_finance(user)


def period(
    contract_id: str | None = Query(None, max_length=60),
    date_from: date | None = None,
    date_to: date | None = None,
):
    """Odrzuca odwrócony zakres bez pomijania filtrów użytkownika."""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="Początek zakresu musi poprzedzać jego koniec.")
    if date_to == date.max:
        raise HTTPException(status_code=422, detail="Nieprawidłowy koniec zakresu.")
    return {"contract_id": contract_id, "date_from": date_from, "date_to": date_to}


async def read(session, function, *args, **kwargs):
    """Uruchamia wyłącznie zapytania lokalnego modelu raportowego."""
    try:
        return await session.run_sync(lambda sync: function(sync.connection(), *args, **kwargs))
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/capabilities")
async def capabilities(session: DatabaseSession, context: AdminContext):
    """Pozwala ukryć zakładkę przed włączeniem flagi funkcji."""
    user = await _require_shipping_access(context, session)
    return {
        "enabled": settings.shipping_orbit_enabled,
        "can_view_finance": can_view_finance(user),
        "can_manage_policy": user.role == "admin",
    }


@router.get("/policies")
async def policies(session: DatabaseSession, context: AdminContext, device_id: str | None = None):
    """Udostępnia efektywne zasady pracownikom uprawnionym do Shipping."""
    await access(session, context)
    device = await read(session, orbit.device_row, device_id) if device_id else None
    return await read(session, orbit_policy.resolve, device)


@router.put("/policies")
async def update_policy(payload: PolicyUpdate, session: DatabaseSession, context: AdminContext):
    """Zapisuje ustawienia i audyt atomowo, wyłącznie jako administrator."""
    await access(session, context)
    admin_session, user = context
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Ustawienia ORBIT zmienia administrator.")
    if payload.scope == "global" and payload.scope_id != "0":
        raise HTTPException(status_code=422, detail="Zakres globalny ma identyfikator 0.")
    if payload.scope == "customer" and (
        not payload.scope_id.isdigit() or int(payload.scope_id) <= 0
    ):
        raise HTTPException(status_code=422, detail="Nieprawidłowy identyfikator klienta.")
    if payload.scope == "device":
        await read(session, orbit.device_row, payload.scope_id)
    result = await read(
        session,
        orbit_policy.save,
        payload.scope,
        payload.scope_id,
        payload.color,
        payload.values.model_dump(exclude_none=True),
        user.id,
    )
    await record_audit(
        session,
        user_id=user.id,
        action="orbit_policy_update",
        payload=result,
        client_ip=getattr(admin_session, "client_ip", None),
    )
    await session.commit()
    return result


@router.post("/shipment-assessment")
async def shipment_assessment(
    payload: AssessmentRequest, session: DatabaseSession, context: AdminContext
):
    """Ocenia szkic z lokalnych danych bez tworzenia etykiety i dokumentów."""
    await access(session, context)
    try:
        return await read(
            session, orbit_policy.assess_drafts, [draft.model_dump() for draft in payload.drafts]
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/shipment-assessment/saved")
async def saved_assessment(
    payload: SavedAssessmentRequest, session: DatabaseSession, context: AdminContext
):
    """Ocenia gotowe zlecenia bez odczytów źródeł i bez generowania etykiety."""
    await access(session, context)
    return await read(session, orbit_policy.assess_saved, payload.order_table_ids)


@router.get("")
async def devices(
    session: DatabaseSession,
    context: AdminContext,
    query: str = Query("", max_length=200),
    scope: Literal["active", "suspended", "scrapped", "review", "all"] = "active",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
):
    """Zwraca urządzenia wraz z jawnym statusem archiwalnym."""
    finance = await access(session, context)
    result = await read(
        session, orbit.list_devices, query=query, scope=scope, page=page, page_size=page_size
    )
    return {**result, "enabled": True, "can_view_finance": finance}


@router.get("/{device_id}")
async def detail(
    session: DatabaseSession,
    context: AdminContext,
    device_id: str,
    filters: Annotated[dict, Depends(period)],
):
    """Udostępnia raport urządzenia bez pobierania źródeł na żądanie."""
    finance = await access(session, context)
    return await read(session, orbit.device_detail, device_id, can_view_finance=finance, **filters)


@router.get("/{device_id}/timeline")
async def timeline(
    session: DatabaseSession,
    context: AdminContext,
    device_id: str,
    filters: Annotated[dict, Depends(period)],
    bucket: Literal["day", "week", "month"] = "month",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
):
    """Zachowuje dokładność miesięcy i rozróżnia zdarzenia od pomiarów."""
    finance = await access(session, context)
    return await read(
        session,
        orbit.timeline,
        device_id,
        bucket=bucket,
        page=page,
        page_size=page_size,
        can_view_finance=finance,
        **filters,
    )


@router.get("/{device_id}/evidence/{event_id}")
async def evidence(session: DatabaseSession, context: AdminContext, device_id: str, event_id: str):
    """Ogranicza dowód do urządzenia oraz prawa odczytu kwot."""
    finance = await access(session, context)
    return await read(session, orbit.evidence, device_id, event_id, can_view_finance=finance)
