"""Ręczne wzbogacanie katalogu zgodności przez OpenAI Web Search."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import AdminAuditLog, ShippingConsumableCompatibility
from app.services.assistant_runtime import load_assistant_runtime_config


class CompatibilityWebError(RuntimeError):
    """Błąd konfiguracji, limitu albo odpowiedzi usługi Web Search."""


def _normalized(value: Any) -> str:
    text = str(value or "").upper()
    replacements = str.maketrans(
        {"Ą": "A", "Ć": "C", "Ę": "E", "Ł": "L", "Ń": "N", "Ó": "O", "Ś": "S", "Ź": "Z", "Ż": "Z"}
    )
    return re.sub(r"[^A-Z0-9]", "", text.translate(replacements))


def _valid_url(value: Any) -> str | None:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return url


def _extract_response(response: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    texts: list[str] = []
    citations: dict[str, dict[str, str]] = {}
    for output in response.get("output") or []:
        if not isinstance(output, dict) or output.get("type") != "message":
            continue
        for content in output.get("content") or []:
            if not isinstance(content, dict):
                continue
            if content.get("type") == "output_text" and content.get("text"):
                texts.append(str(content["text"]))
            for annotation in content.get("annotations") or []:
                if not isinstance(annotation, dict) or annotation.get("type") != "url_citation":
                    continue
                url = _valid_url(annotation.get("url"))
                if url:
                    citations[url] = {
                        "url": url,
                        "title": str(annotation.get("title") or url).strip(),
                    }
    return "\n".join(texts).strip(), list(citations.values())


def _parse_json_text(text: str) -> dict[str, Any]:
    normalized = text.strip()
    if normalized.startswith("```"):
        normalized = re.sub(r"^```(?:json)?\s*", "", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\s*```$", "", normalized)
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError as exc:
        raise CompatibilityWebError(
            "Web Search zwrócił wynik, którego nie można odczytać jako JSON."
        ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("matches"), list):
        raise CompatibilityWebError("Web Search nie zwrócił wymaganej listy `matches`.")
    return payload


def _resolve_model(label: str, models: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidate = _normalized(label)
    if not candidate:
        return None
    exact = [
        model
        for model in models
        if candidate == _normalized(f"{model.get('marka') or ''} {model.get('model') or ''}")
    ]
    if len(exact) == 1:
        return exact[0]
    model_only = [model for model in models if candidate == _normalized(model.get("model"))]
    if len(model_only) == 1:
        return model_only[0]
    return None


async def _daily_usage(session: AsyncSession) -> int:
    since = datetime.now(UTC) - timedelta(days=1)
    rows = list(
        (
            await session.execute(
                select(AdminAuditLog).where(
                    AdminAuditLog.action == "shipping_compatibility_web",
                    AdminAuditLog.created_at >= since,
                )
            )
        ).scalars()
    )
    return sum(int((row.payload or {}).get("requested_items") or 0) for row in rows)


async def _call_web_search(*, api_key: str, prompt: str) -> dict[str, Any]:
    payload = {
        "model": settings.shipping_compatibility_web_model,
        "instructions": (
            "Jesteś katalogerem części urządzeń biurowych. Korzystaj z wyszukiwania WWW. "
            "Nie zgaduj zgodności. Zwróć wyłącznie JSON zgodny z formatem z polecenia."
        ),
        "input": prompt,
        "tools": [{"type": "web_search", "search_context_size": "low"}],
        "tool_choice": "required",
        "max_output_tokens": 3000,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    timeout = httpx.Timeout(settings.shipping_compatibility_web_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            "https://api.openai.com/v1/responses",
            headers=headers,
            json=payload,
        )
    if response.status_code >= 400:
        raise CompatibilityWebError(
            f"OpenAI Responses API zwróciło błąd {response.status_code}: {response.text.strip()}"
        )
    result = response.json()
    if not isinstance(result, dict) or result.get("error"):
        raise CompatibilityWebError("OpenAI Responses API zwróciło nieprawidłową odpowiedź.")
    return result


def _build_prompt(items: list[dict[str, Any]]) -> str:
    public_items = [
        {
            "warehouse_item_id": int(item["warehouse_item_id"]),
            "index": item.get("item_index"),
            "catalog_number_1": item.get("catalog_number_1"),
            "catalog_number_2": item.get("catalog_number_2"),
            "name": item.get("item_name"),
            "brand": item.get("brand"),
            "model_hint": item.get("model"),
        }
        for item in items
    ]
    return (
        "Znajdź modele urządzeń zgodne z poniższymi częściami lub materiałami. "
        "Uznaj zgodność tylko wtedy, gdy potwierdza ją wiarygodna strona producenta, "
        "dystrybutora albo katalog części. Dla braku pewności zwróć pustą listę modeli. "
        "Nie używaj danych klientów ani zleceń.\n\n"
        "Zwróć dokładnie obiekt: "
        '{"matches":[{"warehouse_item_id":123,"models":["Marka Model"],'
        '"source_urls":["https://..."]}]}. '
        "Każdy URL musi pochodzić z użytego wyniku wyszukiwania.\n\n"
        f"Kartoteki: {json.dumps(public_items, ensure_ascii=False)}"
    )


async def enrich_compatibilities_with_web(
    session: AsyncSession,
    *,
    source: dict[str, list[dict[str, Any]]],
    warehouse_item_ids: list[int],
) -> dict[str, Any]:
    """Wyszukuje kandydatów WWW i zapisuje wyłącznie sugestie z cytowaniami."""
    if not settings.shipping_compatibility_web_enabled:
        raise CompatibilityWebError("Wzbogacanie WWW jest wyłączone w konfiguracji.")
    selected_ids = list(dict.fromkeys(int(value) for value in warehouse_item_ids if int(value) > 0))
    batch_limit = max(1, min(settings.shipping_compatibility_web_batch_limit, 20))
    if not selected_ids or len(selected_ids) > batch_limit:
        raise CompatibilityWebError(
            f"Jedna operacja może obejmować od 1 do {batch_limit} kartotek."
        )
    used = await _daily_usage(session)
    if used + len(selected_ids) > settings.shipping_compatibility_web_daily_limit:
        raise CompatibilityWebError("Przekroczono dobowy limit kartotek sprawdzanych przez WWW.")

    runtime = await load_assistant_runtime_config(
        session,
        secret_key=settings.admin_secret_key,
    )
    if not runtime.api_key:
        raise CompatibilityWebError("Brak skonfigurowanego klucza OpenAI.")
    items_by_id = {
        int(item["warehouse_item_id"]): item
        for item in source.get("items", [])
        if item.get("warehouse_item_id") is not None
    }
    selected_items = [items_by_id[item_id] for item_id in selected_ids if item_id in items_by_id]
    if len(selected_items) != len(selected_ids):
        raise CompatibilityWebError(
            "Co najmniej jedna kartoteka nie należy do magazynu fizycznego."
        )

    response = await _call_web_search(
        api_key=runtime.api_key,
        prompt=_build_prompt(selected_items),
    )
    text, citations = _extract_response(response)
    if not citations:
        raise CompatibilityWebError("Web Search nie zwrócił cytowań wymaganych do zapisu sugestii.")
    payload = _parse_json_text(text)
    citations_by_url = {item["url"]: item for item in citations}
    models = source.get("models", [])
    now = datetime.now(UTC)
    created = 0
    refreshed = 0
    skipped = 0

    for match in payload["matches"]:
        if not isinstance(match, dict):
            skipped += 1
            continue
        item_id = int(match.get("warehouse_item_id") or 0)
        item = items_by_id.get(item_id)
        urls = [
            url
            for raw_url in (match.get("source_urls") or [])
            if (url := _valid_url(raw_url)) and url in citations_by_url
        ]
        resolved_models = [
            model
            for label in (match.get("models") or [])
            if isinstance(label, str) and (model := _resolve_model(label, models)) is not None
        ]
        if item is None or not urls or not resolved_models:
            skipped += 1
            continue
        evidence = [
            {
                "source": "web",
                "label": "Zgodność wskazana przez ręcznie uruchomione wyszukiwanie WWW.",
                "url": url,
                "title": citations_by_url[url]["title"],
            }
            for url in dict.fromkeys(urls)
        ]
        for model in resolved_models:
            model_id = int(model["id_model"])
            row = (
                await session.execute(
                    select(ShippingConsumableCompatibility).where(
                        ShippingConsumableCompatibility.firebird_model_id == model_id,
                        ShippingConsumableCompatibility.firebird_warehouse_item_id == item_id,
                    )
                )
            ).scalar_one_or_none()
            if row is not None and row.status in {"confirmed", "rejected"}:
                skipped += 1
                continue
            values = {
                "model_label": " ".join(
                    value
                    for value in (
                        str(model.get("marka") or "").strip(),
                        str(model.get("model") or "").strip(),
                    )
                    if value
                ),
                "item_index": str(item.get("item_index") or "").strip() or None,
                "item_name": str(item.get("item_name") or "").strip() or f"Kartoteka {item_id}",
                "item_kind": str(item.get("item_kind") or "").strip() or None,
                "confidence": "medium",
                "evidence": evidence,
                "last_seen_at": now,
                "updated_at": now,
            }
            if row is None:
                row = ShippingConsumableCompatibility(
                    firebird_model_id=model_id,
                    firebird_warehouse_item_id=item_id,
                    status="suggested",
                    first_seen_at=now,
                    created_at=now,
                    **values,
                )
                session.add(row)
                created += 1
            else:
                for key, value in values.items():
                    setattr(row, key, value)
                if row.status == "stale":
                    row.status = "suggested"
                refreshed += 1
    await session.commit()
    return {
        "requested_items": len(selected_ids),
        "created": created,
        "refreshed": refreshed,
        "skipped": skipped,
        "response_id": response.get("id"),
        "citations": citations,
    }


__all__ = ["CompatibilityWebError", "enrich_compatibilities_with_web"]
