"""API dashboardu obslugi urzadzen."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.models import AdminSetting
from app.services import section_permissions
from app.services.audit import record_audit
from app.services.contracts_dashboard import firebird_writes_enabled
from app.services.device_dashboard import load_device_dashboard_payload
from app.services.device_intake import (
    DeviceIntakeItemInput,
    create_device_intake,
    create_device_intake_batch,
    create_device_model,
    create_device_supplier,
    get_next_ewidencja_suggestion,
    load_device_model_taxonomy,
    search_device_models,
    search_device_suppliers,
    sync_device_catalog_from_models,
)

router = APIRouter(prefix="/admin/device", tags=["admin-device"])

DEVICE_BRANDS_SETTING_KEY = "device.model_brands"
DEFAULT_DEVICE_BRANDS = [
    "Canon",
    "Epson",
    "Konica Minolta",
    "Develop",
    "Kyocera",
    "Utax",
    "Ricoh",
    "Nashuatec",
]


async def _get_or_seed_device_brands(session: AsyncSession) -> list[str]:
    stmt = select(AdminSetting).where(AdminSetting.key == DEVICE_BRANDS_SETTING_KEY)
    setting_row = (await session.execute(stmt)).scalars().first()
    if setting_row is None:
        setting_row = AdminSetting(
            key=DEVICE_BRANDS_SETTING_KEY,
            value=json.dumps(DEFAULT_DEVICE_BRANDS, ensure_ascii=False),
            is_secret=False,
        )
        session.add(setting_row)
        await session.commit()
        return list(DEFAULT_DEVICE_BRANDS)
    try:
        decoded = json.loads(setting_row.value)
    except json.JSONDecodeError:
        decoded = []
    brands = [str(item).strip() for item in decoded if str(item).strip()]
    if not brands:
        brands = list(DEFAULT_DEVICE_BRANDS)
        setting_row.value = json.dumps(brands, ensure_ascii=False)
        await session.commit()
    return brands


class DeviceCatalogSyncRequest(BaseModel):
    """Parametry synchronizacji kartoteki AUTO dla modeli."""

    model_ids: list[int] | None = Field(default=None)
    only_missing: bool = Field(default=True)


class DeviceIntakeRequest(BaseModel):
    """Parametry utworzenia pojedynczego przyjecia PZ."""

    model_id: int = Field(gt=0)
    serial: str = Field(min_length=1, max_length=100)
    ewidencja: str = Field(min_length=1, max_length=100)
    supplier_id: int | None = Field(default=None, gt=0)
    external_document: str | None = Field(default=None, max_length=30)
    issued_by: str | None = Field(default=None, max_length=100)
    force: bool = Field(default=False)


class DeviceIntakeBatchItemRequest(BaseModel):
    """Pojedyncza pozycja egzemplarza dla dokumentu PZ."""

    model_id: int = Field(gt=0)
    serial: str = Field(min_length=1, max_length=100)
    ewidencja: str | None = Field(default=None, max_length=100)
    purchase_price_netto: float | None = Field(default=None, ge=0)


class DeviceIntakeBatchRequest(BaseModel):
    """Parametry utworzenia jednego dokumentu PZ z wieloma pozycjami."""

    items: list[DeviceIntakeBatchItemRequest] = Field(min_length=1, max_length=200)
    supplier_id: int | None = Field(default=None, gt=0)
    external_document: str | None = Field(default=None, max_length=30)
    issued_by: str | None = Field(default=None, max_length=100)
    force: bool = Field(default=False)
    ewidencja_prefix: str | None = Field(default=None, max_length=50)


class DeviceSupplierCreateRequest(BaseModel):
    """Podstawowy payload tworzenia dostawcy KLIENT."""

    name: str = Field(min_length=1, max_length=500)
    nip: str | None = Field(default=None, max_length=30)
    address: str | None = Field(default=None, max_length=250)
    postal_code: str | None = Field(default=None, max_length=6)
    city: str | None = Field(default=None, max_length=150)
    phone: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=200)


class DeviceModelCreateRequest(BaseModel):
    """Payload dodania modelu i ewentualnej kartoteki AUTO."""

    marka: str = Field(min_length=1, max_length=50)
    model: str = Field(min_length=1, max_length=50)
    grupa: str | None = Field(default=None, max_length=50)
    rodzaj: str | None = Field(default=None, max_length=50)
    kolor: bool = Field(default=False)
    plik: str | None = Field(default=None, max_length=250)
    sync_catalog: bool = Field(default=True)


@router.get("/intake/defaults", summary="Pobierz domyslna numeracje ewidencyjna")
async def device_intake_defaults(
    ewidencja_prefix: str | None = Query(default=None, max_length=50),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca kolejne oznaczenie KP wykorzystywane przy autouzupelnianiu formularza."""
    _, admin_user = admin_context
    if admin_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, admin_user, "generator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnien do modulu obslugi urzadzen.",
        )

    try:
        defaults = await asyncio.to_thread(
            get_next_ewidencja_suggestion,
            prefix=ewidencja_prefix,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Blad odczytu numeracji ewidencyjnej: {exc}",
        ) from exc

    return {"ok": True, "defaults": defaults}


@router.get("/models", summary="Wyszukaj modele dla formularza urzadzen")
async def device_models_lookup(
    query: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=500),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca liste modeli wraz z informacja o kartotece AUTO."""
    _, admin_user = admin_context
    if admin_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, admin_user, "generator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnien do modulu obslugi urzadzen.",
        )
    try:
        rows = await asyncio.to_thread(search_device_models, query=query, limit=limit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Blad odczytu listy modeli: {exc}",
        ) from exc
    return {"ok": True, "rows": rows}


@router.get("/model-form-options", summary="Slowniki pola modelu: marka/grupa/rodzaj")
async def device_model_form_options(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca listy do formularza tworzenia modelu (`marka`, `grupa`, `rodzaj`)."""
    _, admin_user = admin_context
    if admin_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, admin_user, "generator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnien do modulu obslugi urzadzen.",
        )
    try:
        brands = await _get_or_seed_device_brands(session)
        taxonomy = await asyncio.to_thread(load_device_model_taxonomy)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Blad odczytu slownikow modelu: {exc}",
        ) from exc
    return {
        "ok": True,
        "options": {
            "brands": brands,
            "default_brand": "Ricoh",
            "groups": taxonomy.get("groups", []),
            "kinds": taxonomy.get("kinds", []),
        },
    }


@router.post("/models", summary="Dodaj nowy model i kartoteke AUTO")
async def device_model_create(
    payload: DeviceModelCreateRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Tworzy MODEL oraz opcjonalnie kartoteke AUTO dla magazynu 28."""
    admin_session, admin_user = admin_context
    if admin_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, admin_user, "generator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnien do modulu obslugi urzadzen.",
        )
    enabled, reason = firebird_writes_enabled()
    if not enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=reason or "Zapis do Firebird jest zablokowany.",
        )
    try:
        result = await asyncio.to_thread(
            create_device_model,
            marka=payload.marka,
            model_name=payload.model,
            grupa=payload.grupa,
            rodzaj=payload.rodzaj,
            kolor=payload.kolor,
            plik=payload.plik,
            sync_catalog=payload.sync_catalog,
            kto=admin_user.email or "CTIP",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Blad tworzenia modelu: {exc}",
        ) from exc

    await record_audit(
        session,
        user_id=admin_user.id,
        action="device_model_create",
        client_ip=admin_session.client_ip,
        payload={
            "id_model": result.get("id_model"),
            "created": result.get("created"),
            "marka": result.get("marka"),
            "model": result.get("model"),
            "catalog": result.get("catalog"),
        },
    )
    await session.commit()
    return {"ok": True, "model": result}


@router.get("/suppliers", summary="Wyszukaj dostawcow KLIENT")
async def device_suppliers_lookup(
    query: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=500),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca liste dostawcow do wyboru po nazwie lub NIP."""
    _, admin_user = admin_context
    if admin_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, admin_user, "generator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnien do modulu obslugi urzadzen.",
        )
    try:
        rows = await asyncio.to_thread(search_device_suppliers, query=query, limit=limit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Blad odczytu listy dostawcow: {exc}",
        ) from exc
    return {"ok": True, "rows": rows}


@router.post("/suppliers", summary="Dodaj podstawowego dostawce KLIENT")
async def device_supplier_create(
    payload: DeviceSupplierCreateRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Tworzy podstawowego dostawce z poziomu formularza urzadzen."""
    admin_session, admin_user = admin_context
    if admin_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, admin_user, "generator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnien do modulu obslugi urzadzen.",
        )
    enabled, reason = firebird_writes_enabled()
    if not enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=reason or "Zapis do Firebird jest zablokowany.",
        )
    try:
        supplier = await asyncio.to_thread(
            create_device_supplier,
            name=payload.name,
            nip=payload.nip,
            address=payload.address,
            postal_code=payload.postal_code,
            city=payload.city,
            phone=payload.phone,
            email=payload.email,
            kto=admin_user.email or "CTIP",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Blad tworzenia dostawcy: {exc}",
        ) from exc

    await record_audit(
        session,
        user_id=admin_user.id,
        action="device_supplier_create",
        client_ip=admin_session.client_ip,
        payload=supplier,
    )
    await session.commit()
    return {"ok": True, "supplier": supplier}


@router.get("/dashboard", summary="Dane dashboardu obslugi urzadzen")
async def device_dashboard_data(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca stan przyjec PZ i problemy danych urzadzen w lokalnej Firebird."""
    _, admin_user = admin_context
    if admin_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, admin_user, "generator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnien do modulu obslugi urzadzen.",
        )

    try:
        return await asyncio.to_thread(load_device_dashboard_payload)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Blad odczytu dashboardu urzadzen: {exc}",
        ) from exc


@router.post("/catalog/sync", summary="Synchronizuj kartoteke AUTO dla modeli")
async def device_catalog_sync(
    payload: DeviceCatalogSyncRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Tworzy/uzupelnia kartoteke AUTO na magazynie 28 dla modeli Firebird."""
    admin_session, admin_user = admin_context
    if admin_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, admin_user, "generator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnien do modulu obslugi urzadzen.",
        )

    enabled, reason = firebird_writes_enabled()
    if not enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=reason or "Zapis do Firebird jest zablokowany.",
        )

    try:
        result = await asyncio.to_thread(
            sync_device_catalog_from_models,
            model_ids=payload.model_ids,
            only_missing=payload.only_missing,
            kto=admin_user.email or "CTIP",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Blad synchronizacji kartoteki AUTO: {exc}",
        ) from exc

    await record_audit(
        session,
        user_id=admin_user.id,
        action="device_catalog_sync",
        client_ip=admin_session.client_ip,
        payload={
            "model_ids": payload.model_ids,
            "only_missing": payload.only_missing,
            "total_models": result.total_models,
            "created": result.created,
            "updated": result.updated,
            "existing": result.existing,
        },
    )
    await session.commit()
    return {
        "ok": True,
        "message": (
            "Synchronizacja kartoteki AUTO zakonczona: "
            f"utworzono={result.created}, zaktualizowano={result.updated}, "
            f"istniejace={result.existing}."
        ),
        "summary": {
            "total_models": result.total_models,
            "created": result.created,
            "updated": result.updated,
            "existing": result.existing,
        },
        "rows": result.rows,
    }


@router.post("/intake", summary="Utworz przyjecie PZ urzadzenia")
async def device_intake_create(
    payload: DeviceIntakeRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Tworzy przyjecie PZ z wpisaniem S/N i numeru KP do procesu SERIAL."""
    admin_session, admin_user = admin_context
    if admin_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, admin_user, "generator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnien do modulu obslugi urzadzen.",
        )

    enabled, reason = firebird_writes_enabled()
    if not enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=reason or "Zapis do Firebird jest zablokowany.",
        )

    try:
        result = await asyncio.to_thread(
            create_device_intake,
            model_id=payload.model_id,
            serial=payload.serial,
            ewidencja=payload.ewidencja,
            supplier_id=payload.supplier_id,
            external_document=payload.external_document,
            issued_by=payload.issued_by,
            force=payload.force,
            kto=admin_user.email or "CTIP",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Blad tworzenia przyjecia PZ: {exc}",
        ) from exc

    await record_audit(
        session,
        user_id=admin_user.id,
        action="device_intake_create",
        client_ip=admin_session.client_ip,
        payload={
            "model_id": result.model_id,
            "serial": result.serial,
            "ewidencja": result.ewidencja,
            "supplier_id": result.supplier_id,
            "warehouse_item_id": result.warehouse_item_id,
            "warehouse_index": result.warehouse_index,
            "pz_id": result.pz_id,
            "pz_number": result.pz_number,
            "zakpozycja_id": result.zakpozycja_id,
            "serial_id": result.serial_id,
            "machine_id": result.machine_id,
            "machine_table_id": result.machine_table_id,
            "purchase_price_netto": float(result.purchase_price_netto or 0),
        },
    )
    await session.commit()

    return {
        "ok": True,
        "message": (
            f"Utworzono przyjecie {result.pz_number}: "
            f"PZ ID {result.pz_id}, ZAKPOZYCJA ID {result.zakpozycja_id}, "
            f"SERIAL ID {result.serial_id}."
        ),
        "intake": {
            "model_id": result.model_id,
            "warehouse_item_id": result.warehouse_item_id,
            "warehouse_index": result.warehouse_index,
            "pz_id": result.pz_id,
            "pz_number": result.pz_number,
            "zakpozycja_id": result.zakpozycja_id,
            "serial_id": result.serial_id,
            "serial": result.serial,
            "ewidencja": result.ewidencja,
            "supplier_id": result.supplier_id,
            "machine_id": result.machine_id,
            "machine_table_id": result.machine_table_id,
            "purchase_price_netto": float(result.purchase_price_netto or 0),
        },
    }


@router.post("/intake/batch", summary="Utworz przyjecie PZ urzadzen (wiele pozycji)")
async def device_intake_batch_create(
    payload: DeviceIntakeBatchRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Tworzy dokument PZ z wieloma egzemplarzami i zaklada rekord MASZYNA dla kazdego."""
    admin_session, admin_user = admin_context
    if admin_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, admin_user, "generator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnien do modulu obslugi urzadzen.",
        )

    enabled, reason = firebird_writes_enabled()
    if not enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=reason or "Zapis do Firebird jest zablokowany.",
        )

    try:
        result = await asyncio.to_thread(
            create_device_intake_batch,
            items=[
                DeviceIntakeItemInput(
                    model_id=item.model_id,
                    serial=item.serial,
                    ewidencja=item.ewidencja,
                    purchase_price_netto=item.purchase_price_netto,
                )
                for item in payload.items
            ],
            supplier_id=payload.supplier_id,
            external_document=payload.external_document,
            issued_by=payload.issued_by,
            force=payload.force,
            ewidencja_prefix=payload.ewidencja_prefix,
            kto=admin_user.email or "CTIP",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Blad tworzenia przyjecia PZ batch: {exc}",
        ) from exc

    await record_audit(
        session,
        user_id=admin_user.id,
        action="device_intake_batch_create",
        client_ip=admin_session.client_ip,
        payload={
            "pz_id": result.pz_id,
            "pz_number": result.pz_number,
            "supplier_id": result.supplier_id,
            "item_count": len(result.items),
            "model_ids": [item.model_id for item in result.items],
            "serial_ids": [item.serial_id for item in result.items],
            "machine_ids": [item.machine_id for item in result.items],
            "purchase_price_netto": [
                float(item.purchase_price_netto or 0) for item in result.items
            ],
        },
    )
    await session.commit()

    return {
        "ok": True,
        "message": (
            f"Utworzono przyjecie {result.pz_number}: "
            f"PZ ID {result.pz_id}, pozycje {len(result.items)}."
        ),
        "batch": {
            "pz_id": result.pz_id,
            "pz_number": result.pz_number,
            "supplier_id": result.supplier_id,
            "items": [
                {
                    "model_id": item.model_id,
                    "warehouse_item_id": item.warehouse_item_id,
                    "warehouse_index": item.warehouse_index,
                    "zakpozycja_id": item.zakpozycja_id,
                    "serial_id": item.serial_id,
                    "serial": item.serial,
                    "ewidencja": item.ewidencja,
                    "machine_id": item.machine_id,
                    "machine_table_id": item.machine_table_id,
                    "purchase_price_netto": float(item.purchase_price_netto or 0),
                }
                for item in result.items
            ],
        },
    }


__all__ = ["router"]
