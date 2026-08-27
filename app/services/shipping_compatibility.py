"""Budowanie i obsługa katalogu zgodności części z modelami urządzeń."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ShippingConsumableCompatibility

CompatibilityStatus = Literal["suggested", "confirmed", "rejected", "stale"]
CompatibilityConfidence = Literal["high", "medium", "low"]

_BRAND_FAMILIES = (
    frozenset({"RICOH", "NASHUATEC", "GESTETNER", "LANIER", "INFOTEC"}),
    frozenset({"KONICA", "MINOLTA", "DEVELOP", "INEO", "BIZHUB"}),
    frozenset({"KYOCERA", "UTAX", "TASKALFA", "ECOSYS"}),
)
_GENERIC_MODEL_WORDS = {
    "PRINTER",
    "COPIER",
    "DRUKARKA",
    "KOPIARKA",
    "URZADZENIE",
    "MFP",
}
_INTERNAL_EVIDENCE_SOURCES = {"name", "catalog", "history"}


def _now() -> datetime:
    return datetime.now(UTC)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _search_terms(value: Any) -> list[str]:
    """Zwraca unikalne wyrazy zapytania bez zależności od ich kolejności."""
    terms: list[str] = []
    seen: set[str] = set()
    for term in re.findall(r"[\w]+", _text(value), flags=re.UNICODE):
        normalized = term.casefold()
        if normalized not in seen:
            terms.append(term)
            seen.add(normalized)
    return terms


def _compatibility_search_conditions(query: str | None) -> list[Any]:
    """Buduje warunki wymagające obecności każdego wyrazu w relacji."""
    conditions = []
    for term in _search_terms(query):
        pattern = f"%{term}%"
        compact_model = func.replace(
            func.replace(ShippingConsumableCompatibility.model_label, " ", ""), "-", ""
        )
        compact_name = func.replace(
            func.replace(ShippingConsumableCompatibility.item_name, " ", ""), "-", ""
        )
        conditions.append(
            or_(
                ShippingConsumableCompatibility.model_label.ilike(pattern),
                ShippingConsumableCompatibility.item_name.ilike(pattern),
                ShippingConsumableCompatibility.item_index.ilike(pattern),
                compact_model.ilike(pattern),
                compact_name.ilike(pattern),
            )
        )
    return conditions


def _normalized(value: Any) -> str:
    text = _text(value).upper()
    replacements = str.maketrans(
        {"Ą": "A", "Ć": "C", "Ę": "E", "Ł": "L", "Ń": "N", "Ó": "O", "Ś": "S", "Ź": "Z", "Ż": "Z"}
    )
    return re.sub(r"[^A-Z0-9]+", " ", text.translate(replacements)).strip()


def _compact(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", _normalized(value))


def _brand_family(value: Any) -> frozenset[str] | None:
    words = set(_normalized(value).split())
    for family in _BRAND_FAMILIES:
        if words & family:
            return family
    return None


def _model_variants(model: dict[str, Any]) -> set[str]:
    brand = _normalized(model.get("marka"))
    model_name = _normalized(model.get("model"))
    words = [word for word in model_name.split() if word not in _GENERIC_MODEL_WORDS]
    variants = {_compact(" ".join(words)), _compact(f"{brand} {' '.join(words)}")}
    if len(words) > 1:
        variants.add(_compact("".join(words[-2:])))
    variants.update(
        word
        for word in words
        if not word.isdigit() and any(character.isdigit() for character in word)
    )
    return {
        value
        for value in variants
        if len(value) >= 4 and any(character.isdigit() for character in value)
    }


def _model_signature(model: dict[str, Any]) -> tuple[str, str] | None:
    """Zwraca sygnaturę serii i numeru, np. `MPC` oraz `3503`."""
    match = re.match(r"^([A-Z]+)(\d{3,5})", _compact(model.get("model")))
    if match is None:
        return None
    return match.group(1), match.group(2)


def _item_model_signatures(value: Any, known_prefixes: set[str]) -> set[tuple[str, str]]:
    """Rozwija skrócone listy modeli, np. `MPC 3003/3503`, do sygnatur."""
    tokens = _normalized(value).split()
    signatures: set[tuple[str, str]] = set()
    active_prefix: str | None = None
    for token in tokens:
        combined_match = re.match(r"^([A-Z]+)(\d{3,5})", token)
        if combined_match is not None:
            token_prefix, number = combined_match.groups()
            combined_prefix = f"{active_prefix or ''}{token_prefix}"
            if combined_prefix in known_prefixes:
                active_prefix = combined_prefix
                signatures.add((active_prefix, number))
            elif token_prefix in known_prefixes:
                active_prefix = token_prefix
                signatures.add((active_prefix, number))
            else:
                active_prefix = None
            continue
        if token.isalpha():
            combined_prefix = f"{active_prefix or ''}{token}"
            if combined_prefix in known_prefixes:
                active_prefix = combined_prefix
            elif token in known_prefixes:
                active_prefix = token
            else:
                active_prefix = None
            continue
        if token.isdigit() and active_prefix and 3 <= len(token) <= 5:
            signatures.add((active_prefix, token))
            continue
        active_prefix = None
    return signatures


def _contains_variant(source: str, variant: str) -> bool:
    start = 0
    while True:
        position = source.find(variant, start)
        if position < 0:
            return False
        before = source[position - 1] if position > 0 else ""
        after_position = position + len(variant)
        after = source[after_position] if after_position < len(source) else ""
        starts_with_digit = variant[0].isdigit()
        ends_with_digit = variant[-1].isdigit()
        if not (starts_with_digit and before.isdigit()) and not (
            ends_with_digit and after.isdigit()
        ):
            return True
        start = position + 1


def _item_search_text(item: dict[str, Any]) -> str:
    return " ".join(
        _text(item.get(key))
        for key in (
            "item_index",
            "catalog_number_1",
            "catalog_number_2",
            "item_name",
            "brand",
            "model",
        )
    )


def _is_serialized_device(item: dict[str, Any]) -> bool:
    item_index = _text(item.get("item_index"))
    item_name = _text(item.get("item_name"))
    return bool(
        re.match(r"^(?:KP|WKP|AUTO)/", item_index, flags=re.IGNORECASE)
        or re.search(r"\bS\s*/?\s*N\s*:|\bNR\.?\s*WEW", item_name, flags=re.IGNORECASE)
    )


def _catalog_values(item: dict[str, Any]) -> set[str]:
    return {
        value
        for value in (
            _compact(item.get("item_index")),
            _compact(item.get("catalog_number_1")),
            _compact(item.get("catalog_number_2")),
        )
        if len(value) >= 3
    }


def _model_toner_codes(model: dict[str, Any]) -> set[str]:
    return {
        value
        for value in (
            _compact(model.get("toner")),
            _compact(model.get("toner_c")),
            _compact(model.get("toner_m")),
            _compact(model.get("toner_y")),
        )
        if len(value) >= 3
    }


def _model_label(model: dict[str, Any]) -> str:
    return " ".join(
        value for value in (_text(model.get("marka")), _text(model.get("model"))) if value
    )


def _source_hash(evidence: list[dict[str, Any]]) -> str:
    encoded = json.dumps(evidence, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def derive_compatibility_candidates(
    source: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Buduje niezatwierdzone kandydatury na podstawie nazw, kodów i historii."""
    models = {
        int(model["id_model"]): model
        for model in source.get("models", [])
        if model.get("id_model") is not None and _text(model.get("model"))
    }
    model_signatures = {
        model_id: signature
        for model_id, model in models.items()
        if (signature := _model_signature(model)) is not None
    }
    known_prefixes = {prefix for prefix, _ in model_signatures.values()}
    items = {
        int(item["warehouse_item_id"]): item
        for item in source.get("items", [])
        if item.get("warehouse_item_id") is not None and not _is_serialized_device(item)
    }
    evidence_by_pair: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)

    for item_id, item in items.items():
        item_text = _item_search_text(item)
        item_signatures = _item_model_signatures(item_text, known_prefixes)
        compact_item = _compact(item_text)
        item_family = _brand_family(item_text)
        catalog_values = _catalog_values(item)
        for model_id, model in models.items():
            model_family = _brand_family(model.get("marka"))
            variants = _model_variants(model)
            explicit = any(_contains_variant(compact_item, variant) for variant in variants) or (
                model_signatures.get(model_id) in item_signatures
            )
            if explicit and (
                item_family is None or model_family is None or item_family == model_family
            ):
                evidence_by_pair[(model_id, item_id)].append(
                    {
                        "source": "name",
                        "label": "Model występuje w nazwie, indeksie lub numerze katalogowym części.",
                        "matched_model": _model_label(model),
                    }
                )
            toner_codes = _model_toner_codes(model)
            matched_codes = sorted(catalog_values & toner_codes)
            if matched_codes:
                evidence_by_pair[(model_id, item_id)].append(
                    {
                        "source": "catalog",
                        "label": "Kod materiału w kartotece modelu odpowiada kodowi części.",
                        "matched_codes": matched_codes,
                    }
                )

    for history in source.get("history", []):
        item_id = int(history.get("warehouse_item_id") or 0)
        model_id = int(history.get("model_id") or 0)
        if item_id not in items or model_id not in models:
            continue
        order_count = int(history.get("order_count") or 0)
        machine_count = int(history.get("machine_count") or 0)
        if order_count <= 0:
            continue
        evidence_by_pair[(model_id, item_id)].append(
            {
                "source": "history",
                "label": "Część była używana w zleceniach dla tego modelu.",
                "order_count": order_count,
                "machine_count": machine_count,
            }
        )

    candidates: list[dict[str, Any]] = []
    for (model_id, item_id), evidence in evidence_by_pair.items():
        signal_types = {entry["source"] for entry in evidence}
        history = next((entry for entry in evidence if entry["source"] == "history"), None)
        if len(signal_types) >= 2:
            confidence: CompatibilityConfidence = "high"
        elif signal_types & {"name", "catalog"}:
            confidence = "medium"
        elif history and (
            int(history.get("order_count") or 0) >= 2 or int(history.get("machine_count") or 0) >= 2
        ):
            confidence = "medium"
        else:
            confidence = "low"
        model = models[model_id]
        item = items[item_id]
        candidates.append(
            {
                "firebird_model_id": model_id,
                "firebird_warehouse_item_id": item_id,
                "model_label": _model_label(model),
                "item_index": _text(item.get("item_index")) or None,
                "item_name": _text(item.get("item_name")) or f"Kartoteka {item_id}",
                "item_kind": _text(item.get("item_kind")) or None,
                "confidence": confidence,
                "evidence": evidence,
                "source_hash": _source_hash(evidence),
            }
        )
    return sorted(
        candidates,
        key=lambda item: (
            {"high": 0, "medium": 1, "low": 2}[item["confidence"]],
            item["model_label"],
            item["item_name"],
        ),
    )


def serialize_compatibility(row: ShippingConsumableCompatibility) -> dict[str, Any]:
    """Serializuje mapowanie do jawnego formatu API."""
    return {
        "id": row.id,
        "firebird_model_id": row.firebird_model_id,
        "firebird_warehouse_item_id": row.firebird_warehouse_item_id,
        "model_label": row.model_label,
        "item_index": row.item_index,
        "item_name": row.item_name,
        "item_kind": row.item_kind,
        "status": row.status,
        "confidence": row.confidence,
        "evidence": row.evidence or [],
        "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "review_note": row.review_note,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def scan_compatibility_catalog(
    session: AsyncSession,
    *,
    source: dict[str, list[dict[str, Any]]],
) -> dict[str, int]:
    """Aktualizuje sugestie, zachowując wszystkie ręczne decyzje operatorów."""
    candidates = derive_compatibility_candidates(source)
    existing_rows = list((await session.execute(select(ShippingConsumableCompatibility))).scalars())
    existing = {
        (row.firebird_model_id, row.firebird_warehouse_item_id): row for row in existing_rows
    }
    now = _now()
    seen: set[tuple[int, int]] = set()
    created = 0
    refreshed = 0
    restored = 0

    for candidate in candidates:
        key = (
            int(candidate["firebird_model_id"]),
            int(candidate["firebird_warehouse_item_id"]),
        )
        seen.add(key)
        row = existing.get(key)
        if row is None:
            session.add(
                ShippingConsumableCompatibility(
                    **candidate,
                    status="suggested",
                    first_seen_at=now,
                    last_seen_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            created += 1
            continue
        row.model_label = candidate["model_label"]
        row.item_index = candidate["item_index"]
        row.item_name = candidate["item_name"]
        row.item_kind = candidate["item_kind"]
        row.confidence = candidate["confidence"]
        row.evidence = candidate["evidence"]
        row.source_hash = candidate["source_hash"]
        row.last_seen_at = now
        row.updated_at = now
        if row.status == "stale":
            row.status = "suggested"
            restored += 1
        else:
            refreshed += 1

    stale = 0
    for row in existing_rows:
        key = (row.firebird_model_id, row.firebird_warehouse_item_id)
        evidence_sources = {
            str(entry.get("source")) for entry in (row.evidence or []) if isinstance(entry, dict)
        }
        if (
            key not in seen
            and row.status == "suggested"
            and evidence_sources & _INTERNAL_EVIDENCE_SOURCES
        ):
            row.status = "stale"
            row.updated_at = now
            stale += 1
    await session.commit()
    return {
        "candidates": len(candidates),
        "created": created,
        "refreshed": refreshed,
        "restored": restored,
        "stale": stale,
    }


async def list_compatibilities(
    session: AsyncSession,
    *,
    status: CompatibilityStatus | None,
    confidence: CompatibilityConfidence | None,
    query: str | None,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    """Zwraca filtrowaną i stronicowaną listę mapowań."""
    conditions = []
    if status:
        conditions.append(ShippingConsumableCompatibility.status == status)
    if confidence:
        conditions.append(ShippingConsumableCompatibility.confidence == confidence)
    conditions.extend(_compatibility_search_conditions(query))
    count_stmt = select(func.count()).select_from(ShippingConsumableCompatibility)
    stmt = select(ShippingConsumableCompatibility)
    if conditions:
        count_stmt = count_stmt.where(*conditions)
        stmt = stmt.where(*conditions)
    total = int((await session.scalar(count_stmt)) or 0)
    rows = (
        await session.execute(
            stmt.order_by(
                ShippingConsumableCompatibility.updated_at.desc(),
                ShippingConsumableCompatibility.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars()
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": [serialize_compatibility(row) for row in rows],
    }


async def list_compatibility_items(
    session: AsyncSession,
    *,
    status: CompatibilityStatus | None,
    confidence: CompatibilityConfidence | None,
    query: str | None,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    """Grupuje filtrowane relacje według fizycznej kartoteki magazynowej."""
    conditions = []
    if status:
        conditions.append(ShippingConsumableCompatibility.status == status)
    if confidence:
        conditions.append(ShippingConsumableCompatibility.confidence == confidence)
    conditions.extend(_compatibility_search_conditions(query))
    stmt = select(ShippingConsumableCompatibility)
    if conditions:
        stmt = stmt.where(*conditions)
    rows = list(
        (
            await session.execute(
                stmt.order_by(
                    ShippingConsumableCompatibility.updated_at.desc(),
                    ShippingConsumableCompatibility.id.desc(),
                )
            )
        ).scalars()
    )
    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        item_id = int(row.firebird_warehouse_item_id)
        group = grouped.setdefault(
            item_id,
            {
                "firebird_warehouse_item_id": item_id,
                "item_index": row.item_index,
                "item_name": row.item_name,
                "item_kind": row.item_kind,
                "mapping_count": 0,
                "status_counts": {},
                "models": [],
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            },
        )
        group["mapping_count"] += 1
        status_counts = group["status_counts"]
        status_counts[row.status] = int(status_counts.get(row.status, 0)) + 1
        group["models"].append(serialize_compatibility(row))
    groups = list(grouped.values())
    start = (page - 1) * page_size
    return {
        "page": page,
        "page_size": page_size,
        "total": len(groups),
        "items": groups[start : start + page_size],
    }


async def review_compatibilities(
    session: AsyncSession,
    *,
    mapping_ids: list[int],
    action: Literal["confirm", "reject"],
    user_id: int,
    note: str | None,
) -> dict[str, int]:
    """Potwierdza albo odrzuca wskazane sugestie w jednej operacji."""
    rows = list(
        (
            await session.execute(
                select(ShippingConsumableCompatibility).where(
                    ShippingConsumableCompatibility.id.in_(mapping_ids)
                )
            )
        ).scalars()
    )
    now = _now()
    status_value = "confirmed" if action == "confirm" else "rejected"
    for row in rows:
        row.status = status_value
        row.reviewed_by = user_id
        row.reviewed_at = now
        row.review_note = _text(note) or None
        row.confirmed_by = user_id if action == "confirm" else None
        row.updated_at = now
    await session.commit()
    return {"requested": len(mapping_ids), "updated": len(rows)}


async def confirm_manual_compatibility(
    session: AsyncSession,
    *,
    model_id: int,
    model_label: str,
    item: dict[str, Any],
    user_id: int,
    note: str | None = None,
) -> ShippingConsumableCompatibility:
    """Tworzy lub potwierdza ręczne mapowanie modelu na kartotekę magazynową."""
    rows = await confirm_manual_compatibilities(
        session,
        models=[{"id_model": model_id, "label": model_label}],
        item=item,
        user_id=user_id,
        note=note,
    )
    return rows[0]


async def confirm_manual_compatibilities(
    session: AsyncSession,
    *,
    models: list[dict[str, Any]],
    item: dict[str, Any],
    user_id: int,
    note: str | None = None,
) -> list[ShippingConsumableCompatibility]:
    """Atomowo tworzy lub potwierdza mapowania jednej części do wielu modeli."""
    item_id = int(item["warehouse_item_id"])
    normalized_models: list[dict[str, Any]] = []
    seen_model_ids: set[int] = set()
    for model in models:
        model_id = int(model["id_model"])
        if model_id not in seen_model_ids:
            normalized_models.append(model)
            seen_model_ids.add(model_id)
    existing_rows = list(
        (
            await session.execute(
                select(ShippingConsumableCompatibility).where(
                    ShippingConsumableCompatibility.firebird_model_id.in_(seen_model_ids),
                    ShippingConsumableCompatibility.firebird_warehouse_item_id == item_id,
                )
            )
        ).scalars()
    )
    existing = {row.firebird_model_id: row for row in existing_rows}
    now = _now()
    evidence = [
        {
            "source": "manual",
            "label": "Mapowanie dodane ręcznie przez operatora.",
        }
    ]
    rows: list[ShippingConsumableCompatibility] = []
    for model in normalized_models:
        model_id = int(model["id_model"])
        model_label = _text(model.get("label")) or " ".join(
            value for value in (_text(model.get("marka")), _text(model.get("model"))) if value
        )
        values = {
            "model_label": model_label or f"Model {model_id}",
            "item_index": _text(item.get("item_index")) or None,
            "item_name": _text(item.get("item_name")) or f"Kartoteka {item_id}",
            "item_kind": _text(item.get("item_kind")) or None,
            "status": "confirmed",
            "confidence": "high",
            "evidence": evidence,
            "source_hash": _source_hash(evidence),
            "last_seen_at": now,
            "reviewed_by": user_id,
            "reviewed_at": now,
            "review_note": _text(note) or None,
            "confirmed_by": user_id,
            "updated_at": now,
        }
        row = existing.get(model_id)
        if row is None:
            row = ShippingConsumableCompatibility(
                firebird_model_id=model_id,
                firebird_warehouse_item_id=item_id,
                first_seen_at=now,
                created_at=now,
                **values,
            )
            session.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
        rows.append(row)
    await session.commit()
    for row in rows:
        await session.refresh(row)
    return rows


__all__ = [
    "confirm_manual_compatibilities",
    "confirm_manual_compatibility",
    "derive_compatibility_candidates",
    "list_compatibility_items",
    "list_compatibilities",
    "review_compatibilities",
    "scan_compatibility_catalog",
    "serialize_compatibility",
]
