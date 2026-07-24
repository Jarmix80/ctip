"""Dashboard obslugi urzadzen oparty o lokalna kopie Firebird."""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.services.firebird_runtime import firebird_connection

WAREHOUSE_DEVICE_ID = settings.fb_warehouse_id
WAREHOUSE_OWNER_CLIENT_ID = settings.fb_warehouse_client_id


def _firebird_connection():
    """Zachowuje punkt rozszerzenia testów, używając konfiguracji runtime."""
    return firebird_connection()


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _condensed(value: Any) -> str:
    return " ".join(_text(value).split())


def _device_key(value: Any) -> str:
    raw = _text(value).upper()
    return "".join(char for char in raw if char.isalnum())


def _flag_text(value: Any) -> str:
    normalized = _text(value).upper()
    if not normalized:
        return ""
    if normalized in {"1", "TAK", "TRUE", "Y"}:
        return "TAK"
    if normalized in {"0", "NIE", "FALSE", "N"}:
        return "NIE"
    return normalized


def _flag_bool(value: Any) -> bool | None:
    normalized = _flag_text(value)
    if normalized == "TAK":
        return True
    if normalized == "NIE":
        return False
    return None


def _model_signature(brand: Any, model: Any) -> str:
    return f"{_condensed(brand).upper()}|{_condensed(model).upper()}"


def _is_numeric_only(value: Any) -> bool:
    text = _text(value)
    return bool(text) and text.isdigit()


def _to_iso(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _issue(code: str, severity: str, message: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "message": message}


def _load_models(
    cursor,
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    cursor.execute(
        """
        SELECT
            ID_MODEL,
            MARKA,
            MODEL,
            GRUPA,
            RODZAJ,
            KOLOR,
            PLIK,
            INNE1,
            INNE2,
            INNE3
        FROM MODEL
        """
    )
    models: list[dict[str, Any]] = []
    by_id: dict[int, dict[str, Any]] = {}
    by_signature: dict[str, list[dict[str, Any]]] = {}
    for row in cursor.fetchall():
        item = {
            "id_model": int(row[0]) if row[0] is not None else None,
            "marka": _text(row[1]),
            "model": _text(row[2]),
            "grupa": _text(row[3]),
            "rodzaj": _text(row[4]),
            "kolor": _flag_text(row[5]),
            "plik": _text(row[6]),
            "inne1": _text(row[7]),
            "inne2": _text(row[8]),
            "inne3": _text(row[9]),
        }
        models.append(item)
        if item["id_model"] is not None:
            by_id[int(item["id_model"])] = item
        signature = _model_signature(item["marka"], item["model"])
        by_signature.setdefault(signature, []).append(item)
    return models, by_id, by_signature


def _build_model_quality(
    models: list[dict[str, Any]], by_signature: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    duplicate_signatures = [
        {
            "signature": signature,
            "count": len(items),
            "marka": items[0]["marka"] if items else "",
            "model": items[0]["model"] if items else "",
            "id_models": [item["id_model"] for item in items if item["id_model"] is not None],
        }
        for signature, items in by_signature.items()
        if len(items) > 1
    ]
    duplicate_signatures.sort(
        key=lambda item: (-int(item["count"]), str(item["marka"]), str(item["model"]))
    )

    return {
        "total": len(models),
        "duplicate_signatures_count": len(duplicate_signatures),
        "missing_grupa_count": sum(1 for item in models if not item["grupa"]),
        "missing_rodzaj_count": sum(1 for item in models if not item["rodzaj"]),
        "missing_kolor_count": sum(1 for item in models if not item["kolor"]),
        "missing_plik_count": sum(1 for item in models if not item["plik"]),
        "top_duplicate_signatures": duplicate_signatures[:12],
    }


def _load_supplier(
    cursor, supplier_id: int | None, cache: dict[int, dict[str, Any]]
) -> dict[str, Any]:
    if not supplier_id:
        return {"id_klient": None, "nazwa": "", "nip": ""}
    if supplier_id in cache:
        return cache[supplier_id]
    cursor.execute(
        """
        SELECT FIRST 1 ID_KLIENT, NAZWA, NIP
        FROM KLIENT
        WHERE ID_KLIENT = ?
        """,
        (int(supplier_id),),
    )
    row = cursor.fetchone()
    result = {
        "id_klient": int(row[0]) if row and row[0] is not None else supplier_id,
        "nazwa": _text(row[1]) if row else "",
        "nip": _text(row[2]) if row else "",
    }
    cache[supplier_id] = result
    return result


def _map_serial_row(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id_serial": int(row[0]) if row[0] is not None else None,
        "id_pz": int(row[1]) if row[1] is not None else None,
        "id_wz": int(row[2]) if row[2] is not None else None,
        "id_rw": int(row[3]) if row[3] is not None else None,
        "id_faktura": int(row[4]) if row[4] is not None else None,
        "id_magpoz": int(row[5]) if row[5] is not None else None,
        "id_magazyn": int(row[6]) if row[6] is not None else None,
        "id_maszyna": int(row[7]) if row[7] is not None else None,
        "serial": _text(row[8]),
        "ewidencja": _text(row[9]),
        "stan": _text(row[10]),
        "data_zaku": _to_iso(row[11]),
        "data_sprz": _to_iso(row[12]),
    }


def _find_serial_row(
    cursor,
    *,
    pz_id: int,
    purchase_id_serial: int | None,
    purchase_serial: str,
    purchase_ewidencja: str,
) -> dict[str, Any] | None:
    base_select = """
        SELECT FIRST 1
            ID_SERIAL,
            ID_PZ,
            ID_WZ,
            ID_RW,
            ID_FAKTURA,
            ID_MAGPOZ,
            ID_MAGAZYN,
            ID_MASZYNA,
            SERIAL,
            EWIDENCJA,
            STAN,
            DATA_ZAKU,
            DATA_SPRZ
        FROM SERIAL
    """

    if purchase_id_serial is not None:
        cursor.execute(
            f"""
            {base_select}
            WHERE ID_SERIAL = ?
            """,
            (int(purchase_id_serial),),
        )
        row = cursor.fetchone()
        if row is not None:
            return _map_serial_row(row)

    serial_key = _device_key(purchase_serial)
    ewidencja_key = _device_key(purchase_ewidencja)
    if not serial_key and not ewidencja_key:
        return None

    conditions: list[str] = []
    params: list[Any] = [pz_id]
    if serial_key:
        conditions.append(
            "UPPER(REPLACE(REPLACE(REPLACE(COALESCE(SERIAL, ''), '/', ''), '-', ''), ' ', '')) = ?"
        )
        params.append(serial_key)
    if ewidencja_key:
        conditions.append(
            "UPPER(REPLACE(REPLACE(REPLACE(COALESCE(EWIDENCJA, ''), '/', ''), '-', ''), ' ', '')) = ?"
        )
        params.append(ewidencja_key)

    cursor.execute(
        f"""
        {base_select}
        WHERE ID_PZ = ?
          AND ({' OR '.join(conditions)})
        ORDER BY ID_SERIAL DESC
        """,
        tuple(params),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return _map_serial_row(row)


def _find_machine_row(cursor, machine_id: int | None, serial_value: str) -> dict[str, Any] | None:
    serial_key = _device_key(serial_value)
    query = """
        SELECT FIRST 1
            ID_MASZYNA,
            ID_MASZYNA_TABLE,
            ID_KLIENT,
            ID_UMOWACPC,
            ID_MODEL,
            MARKA,
            MODEL,
            GRUPA,
            SERIAL,
            EWIDENCJA,
            AKTYWNA,
            KOLOROWA,
            SYNWP,
            V_2010A,
            STOI,
            ADRES,
            MIEJSCOWOSC,
            EMAIL
        FROM MASZYNA
        WHERE ID_MASZYNA = ?
    """
    params: list[Any] = [machine_id or 0]
    if serial_key:
        query += """
           OR UPPER(REPLACE(REPLACE(REPLACE(COALESCE(SERIAL, ''), '/', ''), '-', ''), ' ', '')) = ?
           OR UPPER(REPLACE(REPLACE(REPLACE(COALESCE(SERIAL2, ''), '/', ''), '-', ''), ' ', '')) = ?
        """
        params.extend([serial_key, serial_key])
    query += " ORDER BY ID_MASZYNA DESC"
    cursor.execute(query, tuple(params))
    row = cursor.fetchone()
    if row is None:
        return None
    return {
        "id_maszyna": int(row[0]) if row[0] is not None else None,
        "id_maszyna_table": int(row[1]) if row[1] is not None else None,
        "id_klient": int(row[2]) if row[2] is not None else None,
        "id_umowacpc": int(row[3]) if row[3] is not None else None,
        "id_model": int(row[4]) if row[4] is not None else None,
        "marka": _text(row[5]),
        "model": _text(row[6]),
        "grupa": _text(row[7]),
        "serial": _text(row[8]),
        "ewidencja": _text(row[9]),
        "aktywna": _flag_text(row[10]),
        "kolorowa": _flag_text(row[11]),
        "synwp": int(row[12]) if row[12] is not None else 0,
        "v_2010a": _text(row[13]),
        "stoi": _text(row[14]),
        "adres": _text(row[15]),
        "miejscowosc": _text(row[16]),
        "email": _text(row[17]),
    }


def _resolve_model_row(
    warehouse_model_id: int | None,
    machine_model_id: int | None,
    brand: str,
    model: str,
    models_by_id: dict[int, dict[str, Any]],
    models_by_signature: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any] | None, str, bool]:
    if warehouse_model_id and warehouse_model_id in models_by_id:
        return models_by_id[warehouse_model_id], "MAGAZYN.ID_MODEL", False
    if machine_model_id and machine_model_id in models_by_id:
        return models_by_id[machine_model_id], "MASZYNA.ID_MODEL", False

    signature = _model_signature(brand, model)
    matches = models_by_signature.get(signature, [])
    if len(matches) == 1:
        return matches[0], "MODEL podpis tekstowy", False
    if len(matches) > 1:
        return matches[0], "MODEL podpis tekstowy", True
    return None, "", False


def _build_intake_issues(
    *,
    serial_required: bool,
    purchase_qty: float,
    purchase_serial: str,
    purchase_ewidencja: str,
    purchase_id_serial: int | None,
    warehouse_index: str,
    warehouse_model_id: int | None,
    warehouse_model_name: str,
    serial_row: dict[str, Any] | None,
    machine_row: dict[str, Any] | None,
    model_row: dict[str, Any] | None,
    model_duplicate: bool,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []

    if serial_required and purchase_qty > 1:
        issues.append(
            _issue(
                "SERIAL_QTY_GT_ONE",
                "critical",
                "Przy pozycji z numerami seryjnymi ilosc powinna pozostac rowna 1.",
            )
        )

    if serial_required and not purchase_serial:
        issues.append(
            _issue(
                "PURCHASE_SERIAL_EMPTY",
                "critical",
                "Pozycja PZ wymaga numeru seryjnego, ale pole SERIAL pozostalo puste.",
            )
        )

    if serial_required and not purchase_ewidencja:
        issues.append(
            _issue(
                "PURCHASE_EWIDENCJA_EMPTY",
                "warn",
                "Pozycja PZ nie ma symbolu ewidencyjnego; brak numeru wew na etapie przyjecia.",
            )
        )

    if serial_required and purchase_id_serial is None:
        issues.append(
            _issue(
                "ZAKPOZYCJA_ID_SERIAL_EMPTY",
                "info",
                "ZAKPOZYCJA nie przechowuje ID_SERIAL; powiazanie z SERIAL odbywa sie posrednio.",
            )
        )

    if serial_required and _is_numeric_only(warehouse_index):
        issues.append(
            _issue(
                "MAGAZYN_INDEX_TECHNICAL",
                "info",
                "MAGAZYN.INDEKS zostal nadany technicznie; numer wew trafia do SERIAL.EWIDENCJA.",
            )
        )

    if warehouse_model_id is None:
        issues.append(
            _issue(
                "WAREHOUSE_MODEL_EMPTY",
                "critical",
                "Pozycja magazynowa nie ma ID_MODEL; brak stabilnego powiazania z tabela MODEL.",
            )
        )

    if serial_required and serial_row is None:
        issues.append(
            _issue(
                "SERIAL_ROW_MISSING",
                "critical",
                "Brak rekordu w tabeli SERIAL po zatwierdzeniu PZ.",
            )
        )

    if serial_row is not None and serial_required and not serial_row.get("id_maszyna"):
        issues.append(
            _issue(
                "SERIAL_MACHINE_LINK_EMPTY",
                "warn",
                "SERIAL istnieje, ale nie jest jeszcze powiazany z MASZYNA.",
            )
        )

    if machine_row is None:
        issues.append(
            _issue(
                "MACHINE_MISSING",
                "critical",
                "Brak rekordu MASZYNA dla przyjetego egzemplarza.",
            )
        )
        return issues

    if machine_row.get("id_model") is None:
        issues.append(
            _issue(
                "MACHINE_MODEL_EMPTY",
                "warn",
                "MASZYNA nie przejela ID_MODEL mimo wyboru modelu podczas przyjecia.",
            )
        )

    serial_ewidencja = serial_row.get("ewidencja", "") if serial_row is not None else ""
    machine_ewidencja = _text(machine_row.get("ewidencja"))
    if (
        serial_ewidencja
        and machine_ewidencja
        and _device_key(serial_ewidencja) != _device_key(machine_ewidencja)
    ):
        issues.append(
            _issue(
                "EWIDENCJA_MISMATCH",
                "warn",
                "SERIAL.EWIDENCJA i MASZYNA.EWIDENCJA nie sa zgodne.",
            )
        )

    if not _text(machine_row.get("grupa")):
        issues.append(
            _issue(
                "MACHINE_GROUP_EMPTY",
                "warn",
                "MASZYNA nie ma uzupelnionej grupy urzadzenia.",
            )
        )

    if int(machine_row.get("synwp") or 0) != 1:
        issues.append(
            _issue(
                "MACHINE_SYNWP_OFF",
                "warn",
                "MASZYNA ma SYNWP=0; to wyglada na brak synchronizacji z webpanelem i wymaga potwierdzenia.",
            )
        )

    if model_row is None:
        issues.append(
            _issue(
                "MODEL_MASTER_MISSING",
                "critical",
                "Nie udalo sie jednoznacznie powiazac egzemplarza z tabela MODEL.",
            )
        )
        return issues

    if model_duplicate:
        issues.append(
            _issue(
                "MODEL_DUPLICATE_SIGNATURE",
                "warn",
                "W tabeli MODEL istnieje wiecej niz jeden rekord o tym samym podpisie marka+model.",
            )
        )

    if not _text(model_row.get("rodzaj")):
        issues.append(
            _issue(
                "MODEL_KIND_EMPTY",
                "warn",
                "MODEL nie ma uzupelnionego pola RODZAJ, wiec MASZYNA nie pobiera typu uslugi.",
            )
        )

    if not _text(model_row.get("kolor")):
        issues.append(
            _issue(
                "MODEL_COLOR_EMPTY",
                "warn",
                "MODEL nie ma flagi KOLOR; nie da sie pewnie odtworzyc trybu mono/kolor.",
            )
        )

    if not _text(model_row.get("plik")):
        issues.append(
            _issue(
                "MODEL_IMAGE_EMPTY",
                "warn",
                "MODEL nie ma pliku graficznego PLIK potrzebnego pod przyszly dashboard urzadzenia.",
            )
        )

    model_color = _flag_bool(model_row.get("kolor"))
    machine_color = _flag_bool(machine_row.get("kolorowa"))
    if model_color is not None and machine_color is not None and model_color != machine_color:
        issues.append(
            _issue(
                "MACHINE_COLOR_MISMATCH",
                "warn",
                "Flaga kolorowa w MASZYNA nie zgadza sie z definicja MODEL.",
            )
        )

    if "  " in warehouse_model_name:
        issues.append(
            _issue(
                "MODEL_NAME_NEEDS_NORMALIZATION",
                "info",
                "Model ma niestandaryzowane spacje; warto ujednolicic zapis Ricoh przed automatyzacja.",
            )
        )

    return issues


def _severity_rank(value: str) -> int:
    return {"critical": 3, "warn": 2, "info": 1}.get(value, 0)


def _issue_summary(issues: list[dict[str, str]]) -> dict[str, Any]:
    critical = sum(1 for item in issues if item["severity"] == "critical")
    warn = sum(1 for item in issues if item["severity"] == "warn")
    info = sum(1 for item in issues if item["severity"] == "info")
    highest = "ok"
    if critical:
        highest = "critical"
    elif warn:
        highest = "warn"
    elif info:
        highest = "info"
    return {
        "critical_count": critical,
        "warn_count": warn,
        "info_count": info,
        "highest_severity": highest,
    }


def _has_issue(issues: list[dict[str, str]], code: str) -> bool:
    return any(item["code"] == code for item in issues)


def _dedupe_ordered(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = _text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _build_internal_number_hint(
    purchase_ewidencja: str,
    serial_row: dict[str, Any] | None,
    machine_row: dict[str, Any] | None,
) -> dict[str, Any]:
    serial_ewidencja = _text((serial_row or {}).get("ewidencja"))
    machine_ewidencja = _text((machine_row or {}).get("ewidencja"))

    recommended = ""
    source = ""
    if serial_ewidencja:
        recommended = serial_ewidencja
        source = "SERIAL.EWIDENCJA"
    elif purchase_ewidencja:
        recommended = purchase_ewidencja
        source = "ZAKPOZYCJA.EWIDENCJA"
    elif machine_ewidencja:
        recommended = machine_ewidencja
        source = "MASZYNA.EWIDENCJA"

    values = [value for value in [purchase_ewidencja, serial_ewidencja, machine_ewidencja] if value]
    normalized_values = {_device_key(value) for value in values if _device_key(value)}
    return {
        "recommended": recommended,
        "source": source,
        "purchase": purchase_ewidencja,
        "serial": serial_ewidencja,
        "machine": machine_ewidencja,
        "consistent": len(normalized_values) <= 1,
    }


def _build_process_status(
    issues: list[dict[str, str]],
    serial_row: dict[str, Any] | None,
    machine_row: dict[str, Any] | None,
    model_row: dict[str, Any] | None,
) -> dict[str, str]:
    if _has_issue(issues, "SERIAL_ROW_MISSING"):
        return {
            "code": "serial_missing",
            "severity": "critical",
            "label": "Sprawdz SERIAL",
            "detail": "Zatwierdzone PZ nie utworzylo rekordu SERIAL.",
        }

    if _has_issue(issues, "MACHINE_MISSING"):
        return {
            "code": "machine_missing",
            "severity": "critical",
            "label": "Utworz MASZYNA",
            "detail": "Egzemplarz jest na PZ i w SERIAL, ale nie ma jeszcze karty urzadzenia.",
        }

    if _has_issue(issues, "WAREHOUSE_MODEL_EMPTY") or _has_issue(issues, "MODEL_MASTER_MISSING"):
        return {
            "code": "model_missing",
            "severity": "critical",
            "label": "Powiaz MODEL",
            "detail": "Brakuje jednoznacznego modelu bazowego potrzebnego do automatyzacji.",
        }

    if _has_issue(issues, "MACHINE_MODEL_EMPTY") or _has_issue(issues, "EWIDENCJA_MISMATCH"):
        return {
            "code": "machine_alignment",
            "severity": "warn",
            "label": "Uzupelnij MASZYNA",
            "detail": "Karta MASZYNA istnieje, ale nie przejela jeszcze kompletu danych z przyjecia.",
        }

    if (
        _has_issue(issues, "MODEL_KIND_EMPTY")
        or _has_issue(issues, "MODEL_COLOR_EMPTY")
        or _has_issue(issues, "MODEL_IMAGE_EMPTY")
    ):
        return {
            "code": "model_completion",
            "severity": "warn",
            "label": "Uzupelnij MODEL",
            "detail": "Definicja modelu wymaga domkniecia przed pelna automatyzacja.",
        }

    if _has_issue(issues, "MACHINE_SYNWP_OFF") or _has_issue(issues, "MACHINE_COLOR_MISMATCH"):
        return {
            "code": "service_flags",
            "severity": "warn",
            "label": "Potwierdz flagi",
            "detail": "Sprawdz ustawienia webpanelu i zgodnosc koloru po utworzeniu MASZYNA.",
        }

    if serial_row is None or machine_row is None or model_row is None:
        return {
            "code": "review",
            "severity": "info",
            "label": "Weryfikacja",
            "detail": "Egzemplarz wymaga jeszcze rekonsyliacji danych.",
        }

    return {
        "code": "ready",
        "severity": "ok",
        "label": "Gotowe",
        "detail": "Egzemplarz jest przygotowany do dalszej obslugi w /device.",
    }


def _build_next_actions(
    *,
    issues: list[dict[str, str]],
    purchase_ewidencja: str,
    warehouse_model_id: int | None,
    serial_row: dict[str, Any] | None,
    machine_row: dict[str, Any] | None,
    model_row: dict[str, Any] | None,
    internal_number_hint: dict[str, Any],
) -> list[str]:
    actions: list[str] = []
    recommended_internal_number = _text(internal_number_hint.get("recommended"))

    if _has_issue(issues, "SERIAL_QTY_GT_ONE"):
        actions.append(
            "Dla urzadzen seryjnych utrzymuj 1 sztuke na 1 pozycje PZ; kolejne egzemplarze dodawaj jako osobne wiersze."
        )

    if _has_issue(issues, "PURCHASE_SERIAL_EMPTY"):
        actions.append("Uzupelnij numer seryjny na pozycji PZ przed dalsza obsluga egzemplarza.")

    if recommended_internal_number:
        actions.append(
            f"Jako numer wew przyjmij `{recommended_internal_number}` z `{internal_number_hint['source']}`."
        )

    if _has_issue(issues, "MAGAZYN_INDEX_TECHNICAL"):
        actions.append("Nie traktuj MAGAZYN.INDEKS jako numeru wew dla pozycji seryjnych.")

    if _has_issue(issues, "SERIAL_ROW_MISSING"):
        actions.append("Zweryfikuj zapis PZ i utworzenie rekordu SERIAL dla tego egzemplarza.")

    if _has_issue(issues, "MACHINE_MISSING"):
        actions.append(
            "Po przyjeciu PZ utworz rekord MASZYNA i wybierz numer seryjny z tabeli SERIAL."
        )

    if _has_issue(issues, "SERIAL_MACHINE_LINK_EMPTY") and machine_row is not None:
        actions.append("Powiaz rekord SERIAL z istniejaca karta MASZYNA.")

    if _has_issue(issues, "MACHINE_MODEL_EMPTY") and warehouse_model_id is not None:
        actions.append(f"Przepisz ID_MODEL={warehouse_model_id} z MAGAZYN do MASZYNA.")

    if _has_issue(issues, "EWIDENCJA_MISMATCH") and recommended_internal_number:
        actions.append(
            f"Ujednolic MASZYNA.EWIDENCJA do `{recommended_internal_number}`, aby zgadzala sie z SERIAL."
        )

    if _has_issue(issues, "MACHINE_SYNWP_OFF"):
        actions.append(
            "Potwierdz mapowanie pola SYNWP i wlacz publikacje w webpanelu, jezeli to wlasciwa flaga."
        )

    if _has_issue(issues, "MACHINE_COLOR_MISMATCH") and model_row is not None:
        target_color = _flag_text(model_row.get("kolor")) or "zgodnie z definicja MODEL"
        actions.append(f"Ustaw MASZYNA.KOLOROWA na `{target_color}` zgodnie z rekordem MODEL.")

    if (
        _has_issue(issues, "MODEL_KIND_EMPTY")
        or _has_issue(issues, "MODEL_COLOR_EMPTY")
        or _has_issue(issues, "MODEL_IMAGE_EMPTY")
    ):
        actions.append(
            "Uzupelnij w MODEL pola `RODZAJ`, `KOLOR` i `PLIK`, zanim zautomatyzujesz tworzenie egzemplarza."
        )

    if _has_issue(issues, "WAREHOUSE_MODEL_EMPTY") or _has_issue(issues, "MODEL_MASTER_MISSING"):
        actions.append(
            "Brak modelu bazowego powinien blokowac automatyzacje; zaloz lub popraw rekord MODEL."
        )

    if _has_issue(issues, "MODEL_DUPLICATE_SIGNATURE"):
        actions.append(
            "Rozstrzygnij duplikaty marka+model w tabeli MODEL przed uruchomieniem automatycznego doboru modelu."
        )

    if not actions and serial_row is not None and machine_row is not None:
        actions.append(
            "Egzemplarz jest gotowy do dalszej obslugi, zerowki i powiazania z klientem w /device."
        )

    return _dedupe_ordered(actions)


def _build_process_rules() -> list[str]:
    return [
        "Dla urzadzenia magazynowego przy PZ wlacz opcje `Wymaga podania numerow seryjnych`, bo to wymusza pojedynczy egzemplarz i pozwala zmapowac go na SERIAL.",
        "Przy pozycji seryjnej numer wew traktuj z `SERIAL.EWIDENCJA`; `MAGAZYN.INDEKS` moze byc tylko technicznym identyfikatorem rekordu.",
        "Po utworzeniu `MASZYNA` karta musi przejac ten sam numer seryjny, ten sam numer wew oraz `ID_MODEL` z definicji magazynowej.",
        "Automatyzacja tworzenia egzemplarza powinna blokowac sie, gdy w `MODEL` brakuje `GRUPA`, `RODZAJ`, `KOLOR` albo `PLIK`.",
        "Powielanie kilku takich samych maszyn powinno klonowac dane pozycji i zmieniac tylko serial oraz numer wew, a nie tworzyc pozycje zbiorcza o ilosci > 1.",
    ]


def load_device_dashboard_payload(*, limit: int = 20) -> dict[str, Any]:
    """Buduje payload dashboardu /device na podstawie lokalnej kopii Firebird."""
    connection = _firebird_connection()
    cursor = connection.cursor()
    try:
        models, models_by_id, models_by_signature = _load_models(cursor)
        model_quality = _build_model_quality(models, models_by_signature)
        supplier_cache: dict[int, dict[str, Any]] = {}

        fetch_limit = max(limit * 6, 60)
        cursor.execute(
            f"""
            SELECT FIRST {fetch_limit}
                z.ID_ZAKUPY_TABLE,
                z.NUMER,
                z.RODZAJ_DOK,
                z.DOKUMENT,
                z.DATA_WYST,
                z.DATA_PRZY_WYDA,
                z.ID_MP,
                z.ID_MW,
                z.ID_KLIENT,
                z.DOK_ZEW,
                z.WYSTAWIL,
                z.ODEBRAL,
                zp.ID_ZAKPOZYCJA_TABLE,
                zp.ID_MAGAZYN,
                zp.ID_SERIAL,
                zp.SERIAL,
                zp.EWIDENCJA,
                zp.INDEKS,
                zp.NAZWA,
                zp.ILOSC,
                zp.POBRANO,
                zp.CENA_NETTO,
                m.ID_MAGAZYN_TABLE,
                m.ID_MAGAZYN,
                m.INDEKS,
                m.NAZWA,
                m.SERIAL,
                m.MARKA,
                m.MODEL,
                m.ID_MODEL,
                m.ILOSC,
                m.DATA_Z,
                m.DATA_S
            FROM ZAKUPY z
            JOIN ZAKPOZYCJA zp ON zp.ID_ZAKUPY = z.ID_ZAKUPY_TABLE
            LEFT JOIN MAGAZYN m ON m.ID_MAGAZYN_TABLE = zp.ID_MAGAZYN
            WHERE z.RODZAJ_DOK = 'PZ'
              AND COALESCE(z.ID_MP, 0) = ?
            ORDER BY z.ID_ZAKUPY_TABLE DESC, zp.ID_ZAKPOZYCJA_TABLE DESC
            """,
            (WAREHOUSE_DEVICE_ID,),
        )

        recent_intakes: list[dict[str, Any]] = []
        seen_pz_ids: set[int] = set()
        for row in cursor.fetchall():
            purchase_serial = _text(row[15])
            purchase_ewidencja = _text(row[16])
            warehouse_serial_flag = _flag_text(row[26])
            serial_required = warehouse_serial_flag == "TAK"
            if not serial_required and not purchase_serial and not purchase_ewidencja:
                continue

            pz_id = int(row[0])
            purchase_id_serial = int(row[14]) if row[14] is not None else None
            warehouse_item_id = int(row[22]) if row[22] is not None else None
            serial_row = _find_serial_row(
                cursor,
                pz_id=pz_id,
                purchase_id_serial=purchase_id_serial,
                purchase_serial=purchase_serial,
                purchase_ewidencja=purchase_ewidencja,
            )
            machine_row = _find_machine_row(
                cursor,
                (
                    int(serial_row["id_maszyna"])
                    if serial_row and serial_row.get("id_maszyna")
                    else None
                ),
                purchase_serial,
            )

            supplier = _load_supplier(
                cursor,
                int(row[8]) if row[8] is not None else None,
                supplier_cache,
            )

            warehouse_model_id = int(row[29]) if row[29] is not None else None
            machine_model_id = (
                int(machine_row["id_model"])
                if machine_row and machine_row.get("id_model")
                else None
            )
            warehouse_brand = _text(row[27])
            warehouse_model = _text(row[28])
            model_row, model_resolution, model_duplicate = _resolve_model_row(
                warehouse_model_id,
                machine_model_id,
                warehouse_brand or (machine_row or {}).get("marka", ""),
                warehouse_model or (machine_row or {}).get("model", ""),
                models_by_id,
                models_by_signature,
            )

            issues = _build_intake_issues(
                serial_required=serial_required,
                purchase_qty=float(row[19] or 0),
                purchase_serial=purchase_serial,
                purchase_ewidencja=purchase_ewidencja,
                purchase_id_serial=purchase_id_serial,
                warehouse_index=_text(row[24]),
                warehouse_model_id=warehouse_model_id,
                warehouse_model_name=warehouse_model,
                serial_row=serial_row,
                machine_row=machine_row,
                model_row=model_row,
                model_duplicate=model_duplicate,
            )
            internal_number_hint = _build_internal_number_hint(
                purchase_ewidencja,
                serial_row,
                machine_row,
            )
            process_status = _build_process_status(
                issues,
                serial_row,
                machine_row,
                model_row,
            )
            next_actions = _build_next_actions(
                issues=issues,
                purchase_ewidencja=purchase_ewidencja,
                warehouse_model_id=warehouse_model_id,
                serial_row=serial_row,
                machine_row=machine_row,
                model_row=model_row,
                internal_number_hint=internal_number_hint,
            )
            intake = {
                "pz_id": pz_id,
                "pz_number": _text(row[1]),
                "pz_date": _to_iso(row[5] or row[4]),
                "supplier": supplier,
                "external_document_number": _text(row[9]),
                "issued_by": _text(row[10]),
                "received_by": _text(row[11]),
                "purchase_row_id": int(row[12]),
                "purchase": {
                    "warehouse_item_id": int(row[13]) if row[13] is not None else None,
                    "id_serial": int(row[14]) if row[14] is not None else None,
                    "serial": purchase_serial,
                    "ewidencja": purchase_ewidencja,
                    "index": _text(row[17]),
                    "name": _text(row[18]),
                    "quantity": str(row[19]),
                    "taken_quantity": str(row[20]),
                    "price_net": str(row[21]) if row[21] is not None else "",
                },
                "warehouse": {
                    "id_magazyn_table": warehouse_item_id,
                    "warehouse_id": int(row[23]) if row[23] is not None else None,
                    "index": _text(row[24]),
                    "name": _text(row[25]),
                    "serial_required": serial_required,
                    "serial_flag": warehouse_serial_flag,
                    "marka": warehouse_brand,
                    "model": warehouse_model,
                    "id_model": warehouse_model_id,
                    "quantity": str(row[30]) if row[30] is not None else "",
                    "data_z": _to_iso(row[31]),
                    "data_s": _to_iso(row[32]),
                },
                "serial": serial_row,
                "machine": machine_row,
                "model": {
                    "resolved_from": model_resolution,
                    "duplicate_signature": model_duplicate,
                    "id_model": model_row["id_model"] if model_row else None,
                    "marka": model_row["marka"] if model_row else "",
                    "model": model_row["model"] if model_row else "",
                    "grupa": model_row["grupa"] if model_row else "",
                    "rodzaj": model_row["rodzaj"] if model_row else "",
                    "kolor": model_row["kolor"] if model_row else "",
                    "plik": model_row["plik"] if model_row else "",
                },
                "issues": issues,
                "issue_summary": _issue_summary(issues),
                "process_status": process_status,
                "next_actions": next_actions,
                "internal_number": internal_number_hint,
            }
            recent_intakes.append(intake)
            seen_pz_ids.add(pz_id)
            if len(recent_intakes) >= limit:
                break

        ready_rows = sum(
            1
            for item in recent_intakes
            if item["issue_summary"]["highest_severity"] in {"ok", "info"}
        )
        critical_rows = sum(
            1 for item in recent_intakes if item["issue_summary"]["highest_severity"] == "critical"
        )
        warn_rows = sum(
            1 for item in recent_intakes if item["issue_summary"]["highest_severity"] == "warn"
        )
        serial_linked_rows = sum(1 for item in recent_intakes if item["serial"])
        machine_linked_rows = sum(1 for item in recent_intakes if item["machine"])
        pending_machine_rows = sum(
            1 for item in recent_intakes if item["process_status"]["code"] == "machine_missing"
        )
        alignment_rows = sum(
            1 for item in recent_intakes if item["process_status"]["code"] == "machine_alignment"
        )
        model_gap_rows = sum(
            1
            for item in recent_intakes
            if item["process_status"]["code"] in {"model_missing", "model_completion"}
        )

        return {
            "summary": {
                "pz_count": len(seen_pz_ids),
                "device_rows": len(recent_intakes),
                "serial_linked_rows": serial_linked_rows,
                "machine_linked_rows": machine_linked_rows,
                "ready_rows": ready_rows,
                "critical_rows": critical_rows,
                "warn_rows": warn_rows,
                "pending_machine_rows": pending_machine_rows,
                "alignment_rows": alignment_rows,
                "model_gap_rows": model_gap_rows,
            },
            "recent_intakes": recent_intakes,
            "model_quality": model_quality,
            "process_rules": _build_process_rules(),
            "operational_notes": [
                "Przy PZ z wlaczonymi numerami seryjnymi Menadzer Serwisu zapisuje numer wew glownie do ZAKPOZYCJA.EWIDENCJA i SERIAL.EWIDENCJA.",
                "Gdy pozycja magazynowa ma SERIAL='TAK', MAGAZYN.INDEKS moze zostac nadany technicznie; nie nalezy go traktowac jako zrodla numeru wew.",
                "Pole `symbol ewidencyjny` jest aktualnie najlepszym kandydatem na numer wew, bo trafia do SERIAL.EWIDENCJA i moze byc zgrane z MASZYNA.EWIDENCJA.",
                "Po utworzeniu MASZYNA trzeba potwierdzic zgodnosc EWIDENCJA wzgledem SERIAL oraz uzupelnic ID_MODEL, jezeli desktop nie przeniosl go automatycznie.",
                "Wlaczone numery seryjne wymuszaja 1 sztuke na pozycji PZ, wiec wielokrotny zakup tego samego modelu trzeba rozbijac na osobne egzemplarze.",
                "Tabela MODEL wymaga uporzadkowania przed pelna automatyzacja: brak RODZAJ/KOLOR/PLIK blokuje spójne zakladanie urzadzen.",
                "Flaga MASZYNA.SYNWP jest kandydatem na mapowanie 'dostepne na web panel', ale wymaga potwierdzenia w dalszych testach.",
            ],
        }
    finally:
        cursor.close()
        connection.close()


__all__ = ["load_device_dashboard_payload"]
