"""Interpretacja miesięcznych liczników MS bez nadawania im fikcyjnej daty pomiaru."""

import calendar
from datetime import UTC, date, datetime

from app.services.telemetry.parsers import Reading, fingerprint, number, serial_number
from app.services.telemetry.sources import serializable

CHANNELS = {
    "MONO": "black",
    "KOLOR": "color",
    "MONOA3": "black_a3",
    "KOLORA3": "color_a3",
    "SKAN": "scan",
}


def cpc_reading(row: dict, identities: dict) -> Reading:
    """Zachowuje okres, historyczne identyfikatory i liczniki; faktura nie datuje odczytu."""
    payload = serializable(row)
    serial = serial_number(identities.get(row.get("ID_MASZYNA")))
    period = {
        "start": None,
        "end": None,
        "invoice_linked": bool((number(row.get("ID_FAKTURA")) or 0) > 0),
    }
    result = Reading(
        f"CPC:{row['ID_CPC_TABLE']}",
        "billing_period",
        serial,
        payload,
        precision="month",
        time_basis="billing_period",
    )
    year, month = row.get("ROK"), row.get("MIESIAC")
    if (
        isinstance(year, int)
        and isinstance(month, int)
        and 1900 <= year <= 9999
        and 1 <= month <= 12
    ):
        start = date(year, month, 1)
        period.update(
            start=start.isoformat(),
            end=date(year, month, calendar.monthrange(year, month)[1]).isoformat(),
        )
        if start > datetime.now(UTC).date():
            result.issues.append(("future_billing_period", ""))
    else:
        result.issues.append(("invalid_billing_period", ""))
    payload["__ctip_billing__"] = period
    if not period["invoice_linked"]:
        result.issues.append(("billing_uninvoiced", ""))
    if not all(
        isinstance(row.get(key), int) and row[key] > 0
        for key in ("ID_MASZYNA", "ID_KLIENT", "ID_UMOWACPC")
    ):
        result.issues.append(("billing_identity_missing", ""))
    for suffix, metric in CHANNELS.items():
        for boundary in ("START", "END"):
            value = number(row.get(f"LICZNIK_{suffix}_{boundary}"))
            if value is not None:
                result.measurements[f"billing.{boundary.lower()}.{metric}"] = value
        start_value = result.measurements.get(f"billing.start.{metric}")
        end_value = result.measurements.get(f"billing.end.{metric}")
        if start_value is not None and end_value is not None and end_value < start_value:
            result.issues.append(("billing_counter_decrease", metric))
    result.semantic_key = fingerprint(
        ["ms_cpc", row.get("ID_MASZYNA"), row.get("ID_UMOWACPC"), year, month]
    )
    return result
