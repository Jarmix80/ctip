"""Idempotentny katalog wydajności z zachowaniem dowodów i ręcznych decyzji."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime

from sqlalchemy import select, text, update

from app.models import (
    AdminSetting,
    ShippingConsumableCompatibility,
    TonerYield,
    TonerYieldChange,
    TonerYieldEvidence,
)
from app.schemas.toner_yield import TonerYieldUpdate
from app.services.toner_yield_source import model_key, toner_color

SYNC_KEY = "shipping.toner_yields_sync"
VALUE_FIELDS = ("pages", "status", "source", "basis", "reason", "manual_override")


def lower_compatible_estimate(*, color, family, variants):
    """Wybiera niższą deklarację wyłącznie zgodnych, potwierdzonych wariantów wkładu."""
    if color not in {"black", "cyan", "magenta", "yellow"} or not family or not variants:
        raise ValueError("Szacunek wymaga koloru, rodziny i udokumentowanych wariantów.")
    for variant in variants:
        if (
            variant.get("color") != color
            or variant.get("family") != family
            or variant.get("status") != "confirmed"
            or type(variant.get("pages")) is not int
            or variant["pages"] <= 0
            or not variant.get("source")
        ):
            raise ValueError(
                "Nie można przenieść wydajności z niezgodnego lub niepotwierdzonego wkładu."
            )
    chosen = min(variants, key=lambda variant: variant["pages"])
    return {
        "pages": chosen["pages"],
        "status": "estimated",
        "source": chosen["source"],
        "basis": f"Niższa udokumentowana wydajność zgodnych wariantów rodziny {family}; kolor {color}.",
        "reason": "Szacunek z kompatybilnego wkładu; nie jest deklaracją producenta tego zamiennika.",
    }


class YieldConflict(ValueError):
    """Sygnalizuje zmianę wartości przez innego użytkownika lub import."""


def utcnow():
    """Zwraca wspólny czas UTC dla danych i audytu."""
    return datetime.now(UTC)


async def catalog_lock(session):
    """Serializuje importy i odświeżenia w PostgreSQL, bez blokowania Firebirda."""
    if session.bind.dialect.name == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(841209120)"))


def value_snapshot(item):
    """Wybiera wyłącznie pola edytowalnej wartości do niezmiennego audytu."""
    return {field: getattr(item, field) for field in VALUE_FIELDS}


def item_payload(item):
    """Buduje jawny kontrakt odpowiedzi bez dokumentów zakupu i danych klientów."""
    fields = (
        "item_id",
        "warehouse_id",
        "item_index",
        "name",
        "brand",
        "supplier",
        "sku",
        "ean",
        "color",
        "kind",
        "models",
        "scope",
        "scope_note",
        "revision",
        "synced_at",
        "updated_at",
        *VALUE_FIELDS,
    )
    return {**{field: getattr(item, field) for field in fields}, "stock": float(item.stock)}


async def change_yield(
    session, item_id, payload: TonerYieldUpdate, *, actor_id=None, actor_label="Import", manual=True
):
    """Zapisuje korektę i audyt atomowo, pod warunkiem zgodnej wersji rekordu."""
    item = await session.get(TonerYield, item_id)
    if item is None:
        raise LookupError("Nie znaleziono kartoteki tonera.")
    if item.revision != payload.revision:
        raise YieldConflict(
            "Dane zmieniły się w międzyczasie. Wczytaj aktualną wersję przed zapisem."
        )
    before = value_snapshot(item)
    after = {**payload.model_dump(exclude={"revision"}), "manual_override": manual}
    changed_at = utcnow()
    result = await session.execute(
        update(TonerYield)
        .where(TonerYield.item_id == item_id, TonerYield.revision == payload.revision)
        .values(**after, revision=payload.revision + 1, updated_at=changed_at)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise YieldConflict(
            "Dane zmieniły się w międzyczasie. Wczytaj aktualną wersję przed zapisem."
        )
    session.add(
        TonerYieldChange(
            item_id=item_id,
            revision=payload.revision + 1,
            actor_id=actor_id,
            actor_label=actor_label,
            before=before,
            after=after,
            created_at=changed_at,
        )
    )
    await session.flush()
    await session.refresh(item)
    return item_payload(item)


def scope_for(item, active_keys):
    """Wylicza zakres z modeli i bieżących umów, nie z dawnego statusu kartoteki."""
    if item.scope_review:
        return "review"
    if any(model_key(model["brand"], model["model"]) in active_keys for model in item.models):
        return "active"
    return "inactive" if item.models else "review"


async def refresh_catalog(session, snapshot):
    """Aktualizuje metadane i umowy, nigdy nie zmieniając wydajności ani audytów korekt."""
    await catalog_lock(session)
    warehouse_id = snapshot["warehouse_id"]
    active_keys = set(snapshot["active_keys"])
    synced_at = utcnow()
    current = {
        item.item_id: item
        for item in (
            await session.scalars(select(TonerYield).where(TonerYield.warehouse_id == warehouse_id))
        )
    }
    mappings = list(
        await session.scalars(
            select(ShippingConsumableCompatibility).where(
                ShippingConsumableCompatibility.status.in_(["confirmed", "rejected"])
            )
        )
    )
    by_item = {}
    rejected_by_item = {}
    for mapping in mappings:
        if mapping.status == "rejected":
            rejected_by_item.setdefault(mapping.firebird_warehouse_item_id, set()).add(
                mapping.firebird_model_id
            )
            continue
        model = snapshot["models"].get(mapping.firebird_model_id)
        if model:
            by_item.setdefault(mapping.firebird_warehouse_item_id, []).append(model)
    seen = set()
    for row in snapshot["items"]:
        item_id = row["item_id"]
        seen.add(item_id)
        item = current.get(item_id)
        if item is None:
            item = TonerYield(
                item_id=item_id,
                warehouse_id=warehouse_id,
                name=row["name"],
                models=[],
                excluded_model_ids=[],
                scope_review=False,
                color=toner_color(row["name"]),
                synced_at=synced_at,
                updated_at=synced_at,
            )
            session.add(item)
        for field in ("item_index", "name", "stock"):
            setattr(item, field, row[field])
        if not item.brand:
            item.brand = row["brand"]
        if item.color == "unknown":
            item.color = toner_color(row["name"])
        blocked = set(item.excluded_model_ids) | rejected_by_item.get(item_id, set())
        models = {
            model_key(model["brand"], model["model"]): model
            for model in item.models
            if model.get("id") not in blocked
        }
        for model in by_item.get(item_id, []):
            if model["id"] not in blocked:
                models.setdefault(model_key(model["brand"], model["model"]), model)
        item.models = list(models.values())
        item.scope = scope_for(item, active_keys)
        if not item.scope_review:
            item.scope_note = (
                "Zakres według aktywnych urządzeń i umów MS."
                if item.models
                else "Brak potwierdzonego modelu; sprawdź katalog zgodności."
            )
        item.synced_at = synced_at
    for item_id, item in current.items():
        if item_id not in seen:
            item.scope = "inactive"
            item.scope_note = (
                "Kartoteka nie występuje w ostatnim odczycie tonerów wybranego magazynu."
            )
            item.stock = 0
            item.synced_at = synced_at
    metadata = {
        "synced_at": synced_at.isoformat(),
        "items": len(seen),
        "active_models": len(active_keys),
    }
    setting = await session.scalar(select(AdminSetting).where(AdminSetting.key == SYNC_KEY))
    if setting is None:
        session.add(
            AdminSetting(
                key=SYNC_KEY, value=json.dumps(metadata), is_secret=False, updated_at=synced_at
            )
        )
    else:
        setting.value = json.dumps(metadata)
        setting.updated_at = synced_at
    await session.flush()
    return metadata


async def import_yields(session, document):
    """Importuje wersjonowany pakiet badań bez nadpisywania istniejących wartości."""
    if document.get("format") != "ctip-toner-yields-v1":
        raise ValueError("Nieobsługiwany format importu wydajności.")
    await catalog_lock(session)
    seen = set()
    counts = {"created": 0, "evidence_added": 0, "values_added": 0, "preserved": 0}
    for row in document["items"]:
        item_id = int(row["item_id"])
        if item_id <= 0 or item_id in seen:
            raise ValueError("Powtórzony lub nieprawidłowy identyfikator kartoteki.")
        seen.add(item_id)
        item = await session.get(TonerYield, item_id)
        if item is None:
            fields = (
                "warehouse_id",
                "item_index",
                "name",
                "brand",
                "supplier",
                "sku",
                "ean",
                "color",
                "kind",
                "stock",
                "models",
                "scope",
                "scope_note",
                "scope_review",
                "excluded_model_ids",
            )
            item = TonerYield(
                item_id=item_id,
                **{field: row[field] for field in fields if field in row},
                synced_at=utcnow(),
                updated_at=utcnow(),
            )
            session.add(item)
            await session.flush()
            counts["created"] += 1
        else:
            for field in ("brand", "supplier", "sku", "ean", "color", "kind"):
                if getattr(item, field) in ("", "unknown") and row.get(field) not in (
                    None,
                    "",
                    "unknown",
                ):
                    setattr(item, field, row[field])
            item.excluded_model_ids = sorted(
                set(item.excluded_model_ids) | set(row.get("excluded_model_ids", []))
            )
            item.models = [
                model for model in item.models if model.get("id") not in item.excluded_model_ids
            ]
        fingerprints = set(
            await session.scalars(
                select(TonerYieldEvidence.fingerprint).where(TonerYieldEvidence.item_id == item_id)
            )
        )
        for evidence in row.get("evidence", []):
            fingerprint = hashlib.sha256(
                json.dumps(
                    evidence, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                ).encode()
            ).hexdigest()
            if fingerprint not in fingerprints:
                session.add(
                    TonerYieldEvidence(
                        item_id=item_id,
                        fingerprint=fingerprint,
                        payload=evidence,
                        created_at=utcnow(),
                    )
                )
                fingerprints.add(fingerprint)
                counts["evidence_added"] += 1
        value = row.get("value")
        if row.get("estimate"):
            if value:
                raise ValueError("Kartoteka nie może jednocześnie narzucać wartości i szacunku.")
            value = lower_compatible_estimate(**row["estimate"])
        if value and not item.manual_override and item.status == "missing":
            payload = TonerYieldUpdate(revision=item.revision, **value)
            await change_yield(
                session,
                item_id,
                payload,
                manual=False,
                actor_label="Import udokumentowanych wydajności",
            )
            counts["values_added"] += 1
        elif value:
            counts["preserved"] += 1
    await session.flush()
    return counts


def _compact(value):
    """Ujednolica odstępy i łączniki wyszukiwanych oznaczeń, zachowując plus modelu."""
    return re.sub(r"[\s/._-]+", "", str(value or "").casefold())


async def list_yields(
    session,
    *,
    warehouse_id,
    query="",
    scope="active",
    color="",
    brand="",
    supplier="",
    kind="",
    status="",
    only_available=False,
    page=1,
    page_size=50,
):
    """Filtruje niewielki katalog lokalnie, bez odpytywania MS na każde naciśnięcie klawisza."""
    catalog = list(
        await session.scalars(
            select(TonerYield)
            .where(TonerYield.warehouse_id == warehouse_id)
            .order_by(TonerYield.name, TonerYield.item_id)
        )
    )
    items = []
    for item in catalog:
        if scope and scope != "all" and item.scope != scope:
            continue
        if any(
            value and getattr(item, field) != value
            for field, value in (
                ("color", color),
                ("brand", brand),
                ("supplier", supplier),
                ("kind", kind),
                ("status", status),
            )
        ):
            continue
        if only_available and item.stock <= 0:
            continue
        search = _compact(
            " ".join(
                [
                    item.name,
                    item.item_index,
                    item.sku,
                    item.ean,
                    item.brand,
                    item.supplier,
                    *[f'{model["brand"]} {model["model"]}' for model in item.models],
                ]
            )
        )
        if not all(_compact(term) in search for term in query.split()):
            continue
        items.append(item)
    summary = {
        state: sum(item.status == state for item in items)
        for state in ("confirmed", "estimated", "missing")
    }
    summary.update(total=len(items), review=sum(item.scope == "review" for item in items))
    summary["confirmed_percent"] = round(100 * summary["confirmed"] / len(items), 1) if items else 0
    summary["filled_percent"] = (
        round(100 * (summary["confirmed"] + summary["estimated"]) / len(items), 1) if items else 0
    )
    sync = await session.scalar(select(AdminSetting).where(AdminSetting.key == SYNC_KEY))
    return {
        "items": [item_payload(item) for item in items[(page - 1) * page_size : page * page_size]],
        "total": len(items),
        "page": page,
        "page_size": page_size,
        "summary": summary,
        "facets": {
            field: sorted({getattr(item, field) for item in catalog if getattr(item, field)})
            for field in ("brand", "supplier")
        },
        "sync": json.loads(sync.value) if sync else None,
    }


async def yield_detail(session, item_id):
    """Zwraca kartotekę, wszystkie warianty źródłowe i niezmienną historię wartości."""
    item = await session.get(TonerYield, item_id)
    if item is None:
        raise LookupError("Nie znaleziono kartoteki tonera.")
    evidence = list(
        await session.scalars(
            select(TonerYieldEvidence)
            .where(TonerYieldEvidence.item_id == item_id)
            .order_by(TonerYieldEvidence.id)
        )
    )
    history = list(
        await session.scalars(
            select(TonerYieldChange)
            .where(TonerYieldChange.item_id == item_id)
            .order_by(TonerYieldChange.revision.desc())
        )
    )
    return {
        **item_payload(item),
        "evidence": [row.payload for row in evidence],
        "history": [
            {
                "revision": row.revision,
                "actor": row.actor_label,
                "created_at": row.created_at,
                "before": row.before,
                "after": row.after,
            }
            for row in history
        ],
    }
