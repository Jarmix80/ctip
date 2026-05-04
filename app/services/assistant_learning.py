"""Mechanizmy uczenia i heurystyki NL dla CTIP AI Asystenta."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AssistantUserProfile
from app.services.assistant_tools import AssistantToolResult

_DEVICE_WORDS_RE = re.compile(
    r"\b(urzadzen|urządzen|urządzeń|urzadzeni|urządzeni|drukark|maszyn)\w*\b",
    re.IGNORECASE,
)
_PRINT_WORDS_RE = re.compile(
    r"\b(sredni|średni|srednio|średnio|miesiecz|miesięcz|drukuj|wydruk|licznik)\w*\b",
    re.IGNORECASE,
)
_TOP_WORDS_RE = re.compile(r"\b(top|najwiecej|najwięcej|ranking|najlepsze modele)\b", re.IGNORECASE)
_SERIAL_WORDS_RE = re.compile(r"\b(serial|seryjn)\w*\b", re.IGNORECASE)
_CONTRACT_WORDS_RE = re.compile(r"\b(umow|kontrakt)\w*\b", re.IGNORECASE)
_ACTIVE_WORDS_RE = re.compile(r"\b(aktywn|obowiazuj|obowiązuj)\w*\b", re.IGNORECASE)
_COUNT_WORDS_RE = re.compile(r"\b(ile|ilosc|ilość|liczba|policz|zlicz)\w*\b", re.IGNORECASE)
_MONTHS_BACK_RE = re.compile(
    r"\b(?:ostatni(?:e|ch)?|za)\s+(\d{1,3})\s+(?:mies|miesiecy|miesięcy|miesi(?:a|ą)c(?:y|e|ach)?)\b",
    re.IGNORECASE,
)
_COMPANY_HINT_RE = re.compile(
    r"\b(?:firma|firmy|firmie|dla firmy|dla klienta|klienta|u klienta)\s+([A-Za-z0-9 .,&_\\/-]{2,80})",
    re.IGNORECASE,
)
_MODEL_HINT_RE = re.compile(
    r"\bmodel(?:u|em|owi)?\s+([A-Za-z0-9 ._\\/-]{2,40})",
    re.IGNORECASE,
)
_MODEL_TOKEN_RE = re.compile(
    r"\b((?:MP\s?C|MPC)\s?\d{3,5}[A-Z]?|TASKALFA\s?\d{3,5}[A-Z]?|IR\s?\d{3,5}[A-Z]?)\b",
    re.IGNORECASE,
)
_SERIAL_TOKEN_RE = re.compile(r"\b([A-Z0-9][A-Z0-9._\\/-]{4,})\b", re.IGNORECASE)


def _normalize_key(value: str) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _clean_extracted_value(value: str) -> str:
    cleaned = (value or "").strip()
    cleaned = re.sub(r"[\s,.;:!?]+$", "", cleaned)
    return cleaned


def _extract_months_back(prompt: str) -> int | None:
    match = _MONTHS_BACK_RE.search(prompt or "")
    if not match:
        return None
    try:
        parsed = int(match.group(1))
    except ValueError:
        return None
    return max(1, min(parsed, 120))


def _pick_company_name(prompt: str, aliases: dict[str, str]) -> str | None:
    text = (prompt or "").strip()
    for raw_match in _COMPANY_HINT_RE.findall(text):
        candidate = _clean_extracted_value(raw_match)
        candidate = re.split(
            r"\b(?:za|w|dla|od|na|w\s+okresie)\b",
            candidate,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip()
        if candidate:
            alias_value = aliases.get(_normalize_key(candidate))
            return alias_value or candidate
    return None


def _pick_model_name(prompt: str, aliases: dict[str, str]) -> str | None:
    text = (prompt or "").strip()
    hint_match = _MODEL_HINT_RE.search(text)
    if hint_match:
        candidate = _clean_extracted_value(hint_match.group(1))
        candidate = re.split(
            r"\b(?:za|w|dla|od|od\s+dnia|na|w\s+okresie)\b",
            candidate,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip()
        if candidate:
            alias_value = aliases.get(_normalize_key(candidate))
            return alias_value or candidate
    token_match = _MODEL_TOKEN_RE.search(text)
    if token_match:
        candidate = _clean_extracted_value(token_match.group(1))
        if candidate:
            alias_value = aliases.get(_normalize_key(candidate))
            return alias_value or candidate
    return None


def _pick_serial_number(prompt: str) -> str | None:
    text = (prompt or "").strip()
    if not _SERIAL_WORDS_RE.search(text):
        return None
    serial_hint = re.search(
        r"\b(?:serial|nr\s+seryjny|numer\s+seryjny)\s*[:#-]?\s*([A-Z0-9][A-Z0-9._\\/-]{4,})",
        text,
        re.IGNORECASE,
    )
    if serial_hint:
        return _clean_extracted_value(serial_hint.group(1))
    token_match = _SERIAL_TOKEN_RE.search(text)
    if token_match:
        return _clean_extracted_value(token_match.group(1))
    return None


def infer_business_intent_from_prompt(
    prompt: str,
    *,
    preferences: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Mapuje naturalny prompt na gotowy intent biznesowy, jeśli wzorzec jest jednoznaczny."""
    text = (prompt or "").strip()
    if not text:
        return None

    prefs = preferences if isinstance(preferences, dict) else {}
    learning = (
        prefs.get("business_learning") if isinstance(prefs.get("business_learning"), dict) else {}
    )
    company_aliases = (
        learning.get("company_aliases") if isinstance(learning.get("company_aliases"), dict) else {}
    )
    model_aliases = (
        learning.get("model_aliases") if isinstance(learning.get("model_aliases"), dict) else {}
    )

    if _TOP_WORDS_RE.search(text) and _PRINT_WORDS_RE.search(text):
        return {
            "intent": "top_models_by_volume",
            "months_back": _extract_months_back(text) or 12,
        }

    company_name = _pick_company_name(text, company_aliases)
    if company_name and _PRINT_WORDS_RE.search(text):
        return {
            "intent": "company_monthly_print_summary",
            "company_name": company_name,
            "months_back": _extract_months_back(text) or 12,
        }
    if company_name and _DEVICE_WORDS_RE.search(text):
        return {
            "intent": "devices_by_company",
            "company_name": company_name,
        }

    if (
        _DEVICE_WORDS_RE.search(text)
        and _CONTRACT_WORDS_RE.search(text)
        and _ACTIVE_WORDS_RE.search(text)
    ):
        if _COUNT_WORDS_RE.search(text):
            return {
                "intent": "active_devices_on_contracts_count",
            }
        return {
            "intent": "active_devices_on_contracts",
        }

    model_name = _pick_model_name(text, model_aliases)
    if model_name and _PRINT_WORDS_RE.search(text):
        return {
            "intent": "monthly_average_print_by_model",
            "model_name": model_name,
            "months_back": _extract_months_back(text) or 12,
        }

    serial_number = _pick_serial_number(text)
    if serial_number and _PRINT_WORDS_RE.search(text):
        return {
            "intent": "device_monthly_print_by_serial",
            "serial_number": serial_number,
            "months_back": _extract_months_back(text) or 12,
        }

    return None


def build_learning_prompt_context(preferences: dict[str, Any] | None) -> str:
    """Buduje zwięzły kontekst uczenia do promptu systemowego."""
    if not isinstance(preferences, dict):
        return ""
    learning = preferences.get("business_learning")
    if not isinstance(learning, dict):
        return ""

    company_aliases = (
        learning.get("company_aliases") if isinstance(learning.get("company_aliases"), dict) else {}
    )
    model_aliases = (
        learning.get("model_aliases") if isinstance(learning.get("model_aliases"), dict) else {}
    )
    intent_counts = (
        learning.get("intent_success_counts")
        if isinstance(learning.get("intent_success_counts"), dict)
        else {}
    )

    lines: list[str] = []
    if intent_counts:
        counts_preview = ", ".join(
            f"{k}:{int(v)}"
            for k, v in sorted(intent_counts.items(), key=lambda item: str(item[0]))[:6]
            if isinstance(v, int)
        )
        if counts_preview:
            lines.append(f"Statystyki trafnych intentów użytkownika: {counts_preview}.")

    if company_aliases:
        preview = ", ".join(
            f"{alias}->{value}"
            for alias, value in list(company_aliases.items())[:8]
            if isinstance(alias, str) and isinstance(value, str)
        )
        if preview:
            lines.append(f"Aliasy firm użytkownika: {preview}.")

    if model_aliases:
        preview = ", ".join(
            f"{alias}->{value}"
            for alias, value in list(model_aliases.items())[:8]
            if isinstance(alias, str) and isinstance(value, str)
        )
        if preview:
            lines.append(f"Aliasy modeli użytkownika: {preview}.")

    return " ".join(lines).strip()


def merge_learning_preferences(
    preferences: dict[str, Any] | None,
    *,
    prompt: str,
    tool_results: list[AssistantToolResult],
) -> dict[str, Any]:
    """Aktualizuje pamięć uczenia na podstawie udanych wyników narzędzi."""
    base = dict(preferences) if isinstance(preferences, dict) else {}
    learning = (
        dict(base.get("business_learning"))
        if isinstance(base.get("business_learning"), dict)
        else {}
    )
    intent_success_counts = (
        dict(learning.get("intent_success_counts"))
        if isinstance(learning.get("intent_success_counts"), dict)
        else {}
    )
    company_aliases = (
        dict(learning.get("company_aliases"))
        if isinstance(learning.get("company_aliases"), dict)
        else {}
    )
    model_aliases = (
        dict(learning.get("model_aliases"))
        if isinstance(learning.get("model_aliases"), dict)
        else {}
    )
    recent_examples = (
        list(learning.get("recent_examples"))
        if isinstance(learning.get("recent_examples"), list)
        else []
    )

    inferred = infer_business_intent_from_prompt(prompt, preferences=base) or {}
    inferred_company = (
        str(inferred.get("company_name")).strip()
        if inferred.get("company_name") is not None
        else None
    )
    inferred_model = (
        str(inferred.get("model_name")).strip() if inferred.get("model_name") is not None else None
    )

    for result in tool_results:
        if result.tool_name != "firebird_business_read":
            continue
        if result.status != "success":
            continue
        payload = result.payload if isinstance(result.payload, dict) else {}
        intent = str(payload.get("intent") or "").strip()
        if not intent:
            continue
        intent_success_counts[intent] = int(intent_success_counts.get(intent) or 0) + 1

        criteria = payload.get("criteria") if isinstance(payload.get("criteria"), dict) else {}
        if intent == "devices_by_company":
            company_name = str(criteria.get("company_name") or "").strip()
            if company_name and inferred_company:
                company_aliases[_normalize_key(inferred_company)] = company_name
        if intent == "monthly_average_print_by_model":
            model_name = str(criteria.get("model_name") or "").strip()
            if model_name and inferred_model:
                model_aliases[_normalize_key(inferred_model)] = model_name

        recent_examples.append(
            {
                "prompt": (prompt or "")[:240],
                "intent": intent,
                "recorded_at_utc": datetime.now(UTC).isoformat(),
            }
        )

    learning["intent_success_counts"] = intent_success_counts
    learning["company_aliases"] = dict(list(company_aliases.items())[:120])
    learning["model_aliases"] = dict(list(model_aliases.items())[:120])
    learning["recent_examples"] = recent_examples[-40:]
    base["business_learning"] = learning
    return base


async def load_user_assistant_preferences(
    session: AsyncSession,
    *,
    user_id: int,
) -> dict[str, Any]:
    """Ładuje preferencje profilu asystenta użytkownika (lub pusty obiekt)."""
    stmt = select(AssistantUserProfile).where(AssistantUserProfile.user_id == user_id)
    profile = (await session.execute(stmt)).scalar_one_or_none()
    if profile is None or not isinstance(profile.preferences, dict):
        return {}
    return dict(profile.preferences)


async def update_user_learning_profile(
    session: AsyncSession,
    *,
    user_id: int,
    prompt: str,
    tool_results: list[AssistantToolResult],
) -> None:
    """Persistuje uczenie profilu użytkownika na podstawie bieżącej wiadomości."""
    if not tool_results:
        return
    stmt = select(AssistantUserProfile).where(AssistantUserProfile.user_id == user_id)
    profile = (await session.execute(stmt)).scalar_one_or_none()
    now = datetime.now(UTC)
    current_preferences = profile.preferences if profile is not None else None
    merged = merge_learning_preferences(
        current_preferences if isinstance(current_preferences, dict) else {},
        prompt=prompt,
        tool_results=tool_results,
    )
    if profile is None:
        profile = AssistantUserProfile(
            user_id=user_id,
            personalization_enabled=True,
            preferences=merged,
            memory_notes=None,
            updated_at=now,
        )
        session.add(profile)
    else:
        profile.preferences = merged
        profile.updated_at = now


def render_business_tool_answer(payload: dict[str, Any]) -> str:
    """Tworzy zwięzłą odpowiedź tekstową na podstawie payloadu firebird_business_read."""
    intent = str(payload.get("intent") or "").strip()
    criteria = payload.get("criteria") if isinstance(payload.get("criteria"), dict) else {}
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    if intent == "devices_by_company":
        company_name = str(criteria.get("company_name") or "").strip() or "wskazanej firmy"
        if not rows:
            return f"Nie znalazłem urządzeń dla firmy `{company_name}`."
        lines = [f"Znalazłem {len(rows)} urządzeń dla firmy `{company_name}` (limit odpowiedzi)."]
        for row in rows[:25]:
            if not isinstance(row, dict):
                continue
            model = str(row.get("MODEL") or "").strip() or "-"
            serial = str(row.get("SERIAL") or "").strip() or "-"
            ewidencja = str(row.get("EWIDENCJA") or "").strip() or "-"
            aktywna = str(row.get("AKTYWNA") or "").strip() or "-"
            lines.append(
                f"- MODEL: `{model}` | SERIAL: `{serial}` | EWIDENCJA: `{ewidencja}` | AKTYWNA: `{aktywna}`"
            )
        return "\n".join(lines)

    if intent == "monthly_average_print_by_model":
        model_name = str(criteria.get("model_name") or "").strip() or "wskazany model"
        if not rows:
            return f"Nie znalazłem danych CPC dla modelu `{model_name}` w zadanym okresie."
        overall_total = 0.0
        overall_records = 0
        lines = [f"Średnie miesięczne wydruki dla modelu `{model_name}`:"]
        for row in rows[:24]:
            if not isinstance(row, dict):
                continue
            rok = row.get("ROK")
            miesiac = row.get("MIESIAC")
            lacznie = float(row.get("LACZNIE_SUM") or 0)
            srednio = float(row.get("SREDNIO_NA_URZADZENIE") or 0)
            devices = int(row.get("LICZBA_URZADZEN") or 0)
            overall_total += lacznie
            overall_records += 1
            lines.append(
                f"- {rok}-{int(miesiac):02d}: łącznie={int(lacznie)} | średnio/urządzenie={srednio:.2f} | urządzenia={devices}"
            )
        if overall_records > 0:
            lines.append(
                f"Średnia łączna na miesiąc (z widocznych danych): {overall_total / overall_records:.2f}"
            )
        return "\n".join(lines)

    if intent == "active_devices_on_contracts":
        total_count = payload.get("total_count")
        if not rows:
            return "Nie znalazłem aktywnych urządzeń powiązanych z umowami."
        if isinstance(total_count, int) and total_count >= 0:
            lines = [
                f"Dokładna liczba aktywnych urządzeń na umowach: {total_count}.",
                f"Poniżej przykładowa lista ({len(rows)} rekordów z limitu odpowiedzi):",
            ]
        else:
            lines = [f"Znalazłem {len(rows)} aktywnych urządzeń na umowach (limit odpowiedzi)."]
        for row in rows[:40]:
            if not isinstance(row, dict):
                continue
            klient = str(row.get("KLIENT_NAZWA") or "").strip() or "-"
            model = str(row.get("MODEL") or "").strip() or "-"
            serial = str(row.get("SERIAL") or "").strip() or "-"
            ewidencja = str(row.get("EWIDENCJA") or "").strip() or "-"
            umowa = str(row.get("UMOWA_CPC") or "").strip() or "-"
            aktywna = str(row.get("AKTYWNA_MASZYNA") or "").strip() or "-"
            lines.append(
                f"- KLIENT: `{klient}` | MODEL: `{model}` | SERIAL: `{serial}` | EWIDENCJA: `{ewidencja}` | UMOWA: `{umowa}` | AKTYWNA: `{aktywna}`"
            )
        return "\n".join(lines)

    if intent == "active_devices_on_contracts_count":
        total_count = payload.get("total_count")
        if isinstance(total_count, int) and total_count >= 0:
            return f"Dokładna liczba aktywnych urządzeń na umowach: {total_count}."
        return "Nie udało się policzyć aktywnych urządzeń na umowach."

    if intent == "company_monthly_print_summary":
        company_name = str(criteria.get("company_name") or "").strip() or "wskazana firma"
        if not rows:
            return f"Nie znalazłem miesięcznych danych wydruków dla firmy `{company_name}`."
        lines = [f"Miesięczne podsumowanie wydruków dla firmy `{company_name}`:"]
        for row in rows[:24]:
            if not isinstance(row, dict):
                continue
            rok = row.get("ROK")
            miesiac = row.get("MIESIAC")
            lacznie = int(float(row.get("LACZNIE_SUM") or 0))
            avg_per_device = float(row.get("SREDNIO_NA_URZADZENIE") or 0)
            devices = int(row.get("LICZBA_URZADZEN") or 0)
            lines.append(
                f"- {rok}-{int(miesiac):02d}: łącznie={lacznie} | średnio/urządzenie={avg_per_device:.2f} | urządzenia={devices}"
            )
        return "\n".join(lines)

    if intent == "top_models_by_volume":
        if not rows:
            return "Nie znalazłem danych do rankingu modeli w wybranym okresie."
        lines = ["Top modele wg łącznego wolumenu wydruków:"]
        for index, row in enumerate(rows[:20], start=1):
            if not isinstance(row, dict):
                continue
            model = str(row.get("MODEL") or "").strip() or "-"
            lacznie = int(float(row.get("LACZNIE_SUM") or 0))
            devices = int(row.get("LICZBA_URZADZEN") or 0)
            avg = float(row.get("SREDNIO_NA_URZADZENIE") or 0)
            lines.append(
                f"{index}. `{model}` | łącznie={lacznie} | urządzenia={devices} | średnio/urządzenie={avg:.2f}"
            )
        return "\n".join(lines)

    if intent == "device_monthly_print_by_serial":
        serial = str(criteria.get("serial_number") or "").strip() or "wskazany serial"
        if not rows:
            return f"Nie znalazłem historii wydruków dla serialu `{serial}`."
        lines = [f"Historia miesięczna wydruków dla serialu `{serial}`:"]
        for row in rows[:24]:
            if not isinstance(row, dict):
                continue
            rok = row.get("ROK")
            miesiac = row.get("MIESIAC")
            model = str(row.get("MODEL") or "").strip() or "-"
            serial_row = str(row.get("SERIAL") or "").strip() or "-"
            lacznie = int(float(row.get("LACZNIE_SUM") or 0))
            lines.append(
                f"- {rok}-{int(miesiac):02d} | MODEL={model} | SERIAL={serial_row} | łącznie={lacznie}"
            )
        return "\n".join(lines)

    return "Zakończono odczyt danych biznesowych."


__all__ = [
    "build_learning_prompt_context",
    "infer_business_intent_from_prompt",
    "load_user_assistant_preferences",
    "merge_learning_preferences",
    "render_business_tool_answer",
    "update_user_learning_profile",
]
