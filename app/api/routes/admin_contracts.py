"""API dashboardu obslugi umow."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.services import section_permissions
from app.services.contracts_dashboard import (
    find_client_in_firebird,
    find_device_in_firebird,
    load_devices_from_sheet,
    load_submitted_forms,
    normalize_nip,
)

router = APIRouter(prefix="/admin/contracts", tags=["admin-contracts"])


def _to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).isoformat()
    return value.astimezone(UTC).isoformat()


class ContractActionRequest(BaseModel):
    """Żądanie akcji workflow dla formularza/urządzenia."""

    entity: str = Field(pattern="^(form|device)$")
    action: str = Field(min_length=3, max_length=64)
    target_id: int | None = Field(default=None, ge=1)
    row: int | None = Field(default=None, ge=1)


@router.get("/dashboard", summary="Dane dashboardu obslugi umow")
async def contracts_dashboard_data(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca wypelnione formularze, dopasowanie klienta i urzadzen."""
    _, admin_user = admin_context
    if admin_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, admin_user, "generator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnien do modulu obslugi umow.",
        )

    warnings: list[str] = []

    forms = await load_submitted_forms(session, limit=300)
    await session.commit()

    form_items: list[dict] = []
    for item in forms:
        from app.services.form_generator import (
            decode_submitted_payload,
        )  # import lokalny, aby uniknac cykli

        payload, meta = decode_submitted_payload(item)
        payload = payload or {}
        nip = normalize_nip(str(payload.get("company_nip") or ""))
        firebird_match = await asyncio.to_thread(find_client_in_firebird, nip)
        form_items.append(
            {
                "id": item.id,
                "status": item.status,
                "created_at": _to_iso(item.created_at),
                "submitted_at": _to_iso(item.submitted_at),
                "customer_name": str(payload.get("company_name") or item.customer_name or ""),
                "customer_nip": nip,
                "customer_email": str(payload.get("company_email") or item.customer_email or ""),
                "customer_phone": str(payload.get("company_phone") or item.customer_phone or ""),
                "payload": payload,
                "meta": meta or {},
                "firebird": {
                    "found": firebird_match.found,
                    "id_klient": firebird_match.id_klient,
                    "nazwa": firebird_match.nazwa,
                    "nip": firebird_match.nip,
                    "telefon": firebird_match.telefon,
                    "email": firebird_match.email,
                    "error": firebird_match.error,
                },
                "contract_action": "podlacz_klienta" if firebird_match.found else "utworz_klienta",
            }
        )

    try:
        sheet_devices = await asyncio.to_thread(load_devices_from_sheet)
    except Exception as exc:  # noqa: BLE001
        sheet_devices = []
        warnings.append(f"Blad odczytu arkusza Urzadzenia: {exc}")
    devices_output: list[dict] = []
    for device in sheet_devices:
        match = await asyncio.to_thread(
            find_device_in_firebird, device.get("serial"), device.get("ewidencja")
        )
        devices_output.append(
            {
                "row": int(device.get("row") or 0),
                "serial": device.get("serial") or "",
                "ewidencja": device.get("ewidencja") or "",
                "model": device.get("model") or "",
                "found_in_firebird": match.found_in_firebird,
                "id_maszyna": match.id_maszyna,
                "id_klient": match.id_klient,
                "id_umowacpc": match.id_umowacpc,
                "firebird_error": match.error,
                "sync_action": "podlacz" if match.found_in_firebird else "do_weryfikacji",
            }
        )

    matched_count = sum(1 for item in devices_output if item["found_in_firebird"])
    return {
        "forms_total": len(form_items),
        "devices_total": len(devices_output),
        "devices_matched": matched_count,
        "forms": form_items,
        "devices": devices_output,
        "warnings": warnings,
    }


@router.post("/action", summary="Uruchom akcję workflow dla umów")
async def contracts_dashboard_action(
    payload: ContractActionRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Obsługuje kliknięcie akcji w dashboardzie (tryb roboczy bez zapisu produkcyjnego)."""
    _, admin_user = admin_context
    if admin_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, admin_user, "generator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnien do modulu obslugi umow.",
        )
    await session.commit()

    if payload.entity == "form":
        target_text = f"formularz ID {payload.target_id}" if payload.target_id else "formularz"
    else:
        target_text = f"wiersz urządzenia {payload.row}" if payload.row else "urządzenie"

    return {
        "ok": True,
        "message": (
            f"Uruchomiono akcję '{payload.action}' dla {target_text}. "
            "Tryb bezpieczny: tylko workflow podglądu, bez zapisu do bazy produkcyjnej."
        ),
    }


__all__ = ["router"]
