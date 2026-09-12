"""Prognoza okresowa CPC i doradcze reguły bez blokowania wysyłek."""

import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

DEFAULT_POLICY = {
    "spare_toners": 0,
    "low_percent": 20,
    "lead_days": 7,
    "cpc_stale_days": 45,
    "measurement_stale_days": 3,
}
COLORS = ("black", "cyan", "magenta", "yellow")


def decimal(value):
    """Odrzuca nieskończoności, wartości logiczne i błędne liczby."""
    try:
        result = Decimal(str(value)) if not isinstance(value, bool) else None
        return result if result is not None and result.is_finite() else None
    except (ValueError, InvalidOperation):
        return None


def day(value):
    """Odczytuje jawny dzień bez odgadywania daty zdarzenia."""
    try:
        if isinstance(value, str) and len(value) > 10:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.astimezone(ZoneInfo("Europe/Warsaw")).date()
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def forecasts(events, *, today=None, stale_days=45):
    """Liczy średnie tempo z 3–6 kolejnych pełnych miesięcy jednej umowy.

    Brak miesiąca, konflikt, zmiana umowy lub stary okres zatrzymują prognozę.
    Nie przelicza A3 ponownie na strony ogółem i nie ustala poziomu tonera.
    """
    today = today or datetime.now(ZoneInfo("Europe/Warsaw")).date()
    grouped = defaultdict(list)
    for event in events:
        if event.get("kind") != "billing_period":
            continue
        data = event.get("data", {})
        period = data.get("period", {})
        start = day(data.get("period_start") or period.get("start"))
        end = day(data.get("period_end") or period.get("end"))
        if start is None or end is None or end >= today:
            continue
        grouped[(start, end)].append(event)
    ordered = sorted(grouped, reverse=True)
    output = []
    for metric in ("mono", "color", "scans", "mono_a3", "color_a3"):
        periods, expected_end, contract, previous_start_counter = [], None, None, None
        reason = "Wymagane co najmniej trzy kolejne poprawne miesiące jednej umowy."
        for start, end in ordered[:6]:
            candidates = grouped[(start, end)]
            if len(candidates) != 1:
                reason = "Niejednoznaczny okres CPC."
                break
            event = candidates[0]
            data = event.get("data", {})
            boundary = data.get("billing_counters", {}).get(metric, {})
            current_contract = event.get("contract_id") or data.get("contract_id")
            pages = decimal(data.get("billing", {}).get(metric))
            start_counter = decimal(boundary.get("start"))
            end_counter = decimal(boundary.get("end"))
            full_month = start.day == 1 and end == start.replace(
                day=calendar.monthrange(start.year, start.month)[1]
            )
            if (
                not full_month
                or not current_contract
                or event.get("status") not in {"confirmed", "known"}
                or pages is None
                or pages < 0
                or data.get("counter_reset")
                or expected_end is not None
                and end != expected_end
                or contract is not None
                and str(current_contract) != contract
                or previous_start_counter is not None
                and end_counter is not None
                and end_counter != previous_start_counter
            ):
                break
            contract = str(current_contract)
            periods.append((start, end, pages, event.get("id")))
            expected_end = start - timedelta(days=1)
            previous_start_counter = start_counter
        value = {
            "metric": metric,
            "status": "insufficient_data",
            "basis": "ms_cpc",
            "reason": reason,
            "periods": len(periods),
            "daily_pages": None,
            "since_period_pages": None,
            "next_30_days_pages": None,
            "period_end": periods[0][1].isoformat() if periods else None,
            "calculated_at": today.isoformat(),
            "evidence_ids": [period[3] for period in periods],
        }
        if len(periods) >= 3:
            age = (today - periods[0][1]).days
            if age > stale_days:
                value.update(status="stale", reason="Ostatni okres CPC jest zbyt stary.")
            else:
                days = sum((end - start).days + 1 for start, end, _, _ in periods)
                rate = sum((period[2] for period in periods), Decimal(0)) / days
                value.update(
                    status="estimated",
                    reason="Średnie tempo rozliczonego zużycia; nie pomiar bieżący ani poziom tonera.",
                    daily_pages=str(rate.quantize(Decimal("0.01"))),
                    since_period_pages=str((rate * age).quantize(Decimal("1"))),
                    next_30_days_pages=str((rate * 30).quantize(Decimal("1"))),
                )
        output.append(value)
    return output


def assessment(toners, policies, requested, pending=None, *, today=None):
    """Wyjaśnia ryzyko kolejnej dostawy; nie deklaruje rzeczywistego zapasu klienta."""
    today = today or datetime.now(ZoneInfo("Europe/Warsaw")).date()
    pending = pending or {}
    by_color = {item.get("color"): item for item in toners}
    results = []
    for color, quantity in requested.items():
        quantity = decimal(quantity)
        if color not in COLORS or quantity is None or quantity <= 0:
            continue
        policy = {**DEFAULT_POLICY, **policies.get(color, {})}
        toner = by_color.get(color, {})
        observed = day(toner.get("observed_at"))
        level = decimal(toner.get("remaining_percent"))
        fresh = (
            observed is not None
            and 0 <= (today - observed).days <= policy["measurement_stale_days"]
        )
        reliable = (
            fresh
            and toner.get("status") in {"known", "confirmed", "measured", "estimated"}
            and level is not None
            and 0 <= level <= 100
        )
        reasons = []
        underway = decimal(pending.get(color)) or Decimal(0)
        if underway > 0:
            reasons.append(
                "Dostępny jest już niedoręczony lub nierozstrzygnięty transport tego koloru."
            )
        if quantity > Decimal(1 + policy["spare_toners"]):
            reasons.append("Ilość przekracza jeden wkład do urządzenia i dozwolony zapas.")
        remaining_days = decimal(toner.get("remaining_days"))
        timely = remaining_days is not None and remaining_days <= policy["lead_days"]
        if reliable and level > policy["low_percent"] and not timely and not policy["spare_toners"]:
            reasons.append("Poziom tonera jest powyżej progu dostawy; zapas domyślnie wynosi zero.")
        stock = decimal(toner.get("stock_quantity"))
        if reliable and stock is not None and stock > policy["spare_toners"]:
            reasons.append("Historia wskazuje potencjalny zapas; wymaga potwierdzenia u klienta.")
        results.append(
            {
                "color": color,
                "quantity": str(quantity),
                "status": "warning" if reasons else "ok" if reliable else "insufficient_data",
                "reasons": reasons
                or [
                    (
                        "Brak ostrzeżeń na podstawie dostępnych danych."
                        if reliable
                        else "Brak świeżej, wiarygodnej podstawy oceny; decyzja należy do pracownika."
                    )
                ],
                "policy": policy,
                "observed_at": toner.get("observed_at"),
                "basis": toner.get("basis"),
                "stock_basis": "potential",
                "blocking": False,
            }
        )
    return {
        "status": (
            "warning"
            if any(item["status"] == "warning" for item in results)
            else (
                "ok"
                if results and all(item["status"] == "ok" for item in results)
                else "insufficient_data"
            )
        ),
        "items": results,
        "blocking": False,
        "calculated_at": today.isoformat(),
    }


def toner_forecasts(toners, forecast, counters, *, today=None, measurement_stale_days=3):
    """Szacuje czas wyczerpania bez sumowania mono i koloru w licznik ogółem."""
    today = today or datetime.now(ZoneInfo("Europe/Warsaw")).date()
    rates = {
        item["metric"]: decimal(item["daily_pages"])
        for item in forecast
        if item["status"] == "estimated"
    }
    total_rates = []
    for series in counters:
        if series.get("metric") != "total":
            continue
        points = [point for point in series.get("points", []) if day(point.get("observed_at"))]
        points.sort(key=lambda point: point["observed_at"])
        if len(points) < 2:
            continue
        last = points[-1]
        ending = day(last["observed_at"])
        recent = [
            point
            for point in points
            if 7 <= (ending - day(point["observed_at"])).days <= 30
            and point.get("epoch") == last.get("epoch")
        ]
        if not recent or not 0 <= (today - ending).days <= measurement_stale_days:
            continue
        first = recent[0]
        between = [
            point
            for point in points
            if first["observed_at"] <= point["observed_at"] <= last["observed_at"]
        ]
        if any(point.get("status") not in {"known", "confirmed"} for point in between):
            continue
        start_value, end_value = decimal(first.get("value")), decimal(last.get("value"))
        if start_value is not None and end_value is not None and end_value >= start_value:
            total_rates.append(
                (ending, (end_value - start_value) / (ending - day(first["observed_at"])).days)
            )
    if total_rates:
        latest = max(item[0] for item in total_rates)
        candidates = {item[1] for item in total_rates if item[0] == latest}
        if len(candidates) == 1:
            rates["total"] = next(iter(candidates))
    result = []
    for toner in toners:
        rate = rates.get("total" if toner.get("color") == "black" else "color")
        remaining = decimal(toner.get("remaining_pages"))
        observed = day(toner.get("observed_at"))
        value = {**toner, "remaining_days": None}
        if (
            rate is not None
            and rate > 0
            and remaining is not None
            and remaining >= 0
            and observed
            and 0 <= (today - observed).days <= measurement_stale_days
        ):
            value["remaining_days"] = str(
                max(Decimal(0), remaining / rate - (today - observed).days).quantize(Decimal("0.1"))
            )
            value["remaining_days_basis"] = (
                "Szacunek z poziomu, wydajności i tempa; nie potwierdzona data wyczerpania."
            )
        result.append(value)
    return result
