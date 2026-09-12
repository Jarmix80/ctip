"""Czyste kalkulacje ORBIT dla historii jednego urządzenia, bez dostępu do usług.

Adapter przekazuje zdarzenia z polami ``id``, ``kind``, ``source``, ``status``,
``observed_at``, ``time_precision`` oraz słownikiem ``data``. Brak statusu oznacza
niezakwestionowany fakt; jawny status niepewny blokuje wykorzystanie wartości.
Daty mają format ISO, precyzja to ``datetime``/``second``, ``date`` lub ``month``.
CPC pozostaje faktem okresowym; ``billing_period`` nie trafia do serii liczników.
Adapter może przekazać ``billing_counter`` ze źródłem ``ms_cpc``, precyzją
``month``, pustym observed_at oraz ``data.period`` (start/end). Taki punkt
tworzy osobną serię miesięczną z delta=None. Dane wejściowe nie są modyfikowane.

Liczniki są w ``data.counters``: mono, color, scans, total. Flagi ``data.reset``
i ``data.gap`` przyjmują True albo listę metryk; ``counter_reset`` jest aliasem
``reset``. Zdarzenie ``unavailable`` oznacza jawną lukę. ``data.counter_source`` wskazuje
pochodzenie licznika, gdy zdarzenie dostawy pochodzi z innego systemu.

Tonery używają ``data.model_id``, ``model``, ``color`` (black/cyan/magenta/yellow),
``toner_levels`` (słownik kolor: procent), ``item_id``, ``quantity`` oraz
``replacement_confirmed``. Pojedynczy ``toner_level`` może mieć ``level_percent``.
Wydajności to lista rekordów ``item_id``, ``models``, ``color``, ``pages``, ``status``
(confirmed/estimated), opcjonalnie ``source``. Element models to nazwa modelu lub
słownik id/model_id/model. Czarny toner korzysta z total, pozostałe z color;
jawne ``metric`` katalogu może wskazać mono dla modelu monochromatycznego.
Model i kolor muszą być zgodne; adapter odpowiada za rozpoznanie nazw modeli.
Każdy wynik zawiera jawny status, podstawę oraz uzasadnienie po polsku.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from typing import Any

METRICS = ("mono", "color", "scans", "total")
COLORS = ("black", "cyan", "magenta", "yellow")
VALID_STATUSES = {"known", "confirmed", "measured", "observed", "ok", "source_confirmed"}


def _decimal(value: Any) -> Decimal | None:
    """Odczytuje skończoną liczbę dziesiętną bez arytmetyki zmiennoprzecinkowej."""
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _text_number(value: Decimal | None) -> str | None:
    """Zapisuje liczbę bez wykładnika i zbędnych zer części ułamkowej."""
    if value is None:
        return None
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _money(value: Decimal) -> str:
    """Zaokrągla końcową wartość do groszy zgodnie z regułą połowy w górę."""
    with localcontext() as context:
        context.prec = 80
        return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def purchase_cost(data: Mapping[str, Any]) -> dict[str, Any]:
    """Liczy historyczną cenę zakupu razy ilość wydaną pomniejszoną o zwroty.

    Pola: role, purchase_price, issued_quantity, returned_quantity (domyślnie 0),
    purchase_value, document_purchase_price, zero_cost_confirmed i currency
    (domyślnie PLN). ``purchase_value`` uzgadnia cenę razy ilość przed zwrotami.
    Cena dokumentu służy wyłącznie uzgodnieniu, nigdy zastąpieniu brakującej ceny.
    Status: known, unknown, planned, conflict lub excluded. Kwota nieznana to None.
    """
    result = {
        "amount": None,
        "status": "unknown",
        "reason": "Brak wiarygodnych danych kosztu zakupu.",
        "currency": "PLN",
        "quantity": None,
    }

    def finish(status: str, reason: str, amount: str | None = None) -> dict[str, Any]:
        """Kończy obliczenie z jawną przyczyną dostępności kosztu."""
        return {**result, "status": status, "reason": reason, "amount": amount}

    if data.get("role") != "contract":
        return finish("excluded", "Pozycja nie należy do kosztów umowy.")
    if str(data.get("currency", "PLN")).upper() != "PLN":
        return finish("unknown", "Waluta zakupu nie jest PLN; brak przeliczenia waluty.")
    issued = _decimal(data.get("issued_quantity"))
    returned = _decimal(data.get("returned_quantity", 0))
    if issued is None or returned is None:
        return finish("unknown", "Brak poprawnej ilości wydanej lub zwróconej.")
    if issued < 0 or returned < 0 or returned > issued:
        return finish("conflict", "Niespójność ilości wydanej i zwrotów.")
    with localcontext() as context:
        context.prec = 80
        quantity = issued - returned
        result["quantity"] = _text_number(quantity)
        if issued == 0:
            return finish("planned", "Pozycja planowana: nie potwierdzono wydania.")
        price = _decimal(data.get("purchase_price"))
        if price is None:
            return finish(
                "unknown", "Brak historycznej ceny zakupu; cena sprzedaży nie jest kosztem."
            )
        if price < 0:
            return finish("conflict", "Historyczna cena zakupu jest ujemna.")
        for field, expected in (
            ("document_purchase_price", price),
            ("purchase_value", price * issued),
        ):
            if data.get(field) is None:
                continue
            actual = _decimal(data[field])
            if actual is None:
                return finish("unknown", "Niepoprawne dane uzgodnienia zakupu z dokumentem.")
            agrees = (
                actual == expected
                if field == "document_purchase_price"
                else _money(actual) == _money(expected)
            )
            if not agrees:
                return finish("conflict", "Konflikt ceny lub wartości zakupu z dokumentem.")
        if price == 0 and data.get("zero_cost_confirmed") is not True:
            return finish("unknown", "Techniczne zero ceny zakupu nie potwierdza zerowego kosztu.")
        return finish(
            "known",
            "Historyczna cena zakupu pomnożona przez ilość wydaną po zwrotach.",
            _money(price * quantity),
        )


def _data(event: Mapping[str, Any]) -> Mapping[str, Any]:
    """Zwraca słownik faktów zdarzenia albo pusty słownik."""
    value = event.get("data")
    return value if isinstance(value, Mapping) else {}


def _valid(event: Mapping[str, Any]) -> bool:
    """Dopuszcza jedynie fakty bez jawnego zastrzeżenia jakości."""
    return event.get("status", "known") in VALID_STATUSES


def _instant(event: Mapping[str, Any]) -> datetime | None:
    """Odczytuje faktyczny czas; okresy i CPC nie otrzymują sztucznej daty."""
    if (
        event.get("time_precision") in {"month", "unknown"}
        or event.get("source") == "ms_cpc"
        or event.get("kind") == "billing_period"
    ):
        return None
    value = event.get("observed_at")
    try:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, date):
            parsed = datetime.combine(value, datetime.min.time())
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _observed(event: Mapping[str, Any]) -> str | None:
    """Zachowuje oryginalną datę i nie korzysta z czasu importu."""
    value = event.get("observed_at")
    return value.isoformat() if isinstance(value, date) else value


def _precision(event: Mapping[str, Any]) -> str:
    """Zachowuje precyzję i rozpoznaje datę bez części godzinowej."""
    if event.get("source") == "ms_cpc":
        return "month"
    if event.get("time_precision"):
        return event["time_precision"]
    observed = _observed(event)
    return "date" if observed and len(observed) == 10 else "datetime"


def _source(event: Mapping[str, Any]) -> str | None:
    """Oddziela źródło licznika od źródła dokumentu dostawy."""
    return _data(event).get("counter_source") or event.get("source")


def _flag(event: Mapping[str, Any], name: str, metric: str) -> bool:
    """Rozpoznaje jawną lukę lub reset wszystkich bądź wybranych liczników."""
    value = _data(event).get(name, _data(event).get("counter_reset") if name == "reset" else None)
    return (
        value is True
        or isinstance(value, (list, tuple, set))
        and metric in value
        or event.get("kind") == {"reset": "counter_reset", "gap": "telemetry_gap"}[name]
        or name == "gap"
        and event.get("kind") == "unavailable"
    )


def _time_key(event: Mapping[str, Any]) -> tuple[datetime | None, str]:
    """Utożsamia sekundową i pełną precyzję bez podnoszenia dokładności daty."""
    return _instant(event), "date" if _precision(event) == "date" else "datetime"


def _event_order(event: Mapping[str, Any]) -> tuple[datetime, str]:
    """Porządkuje odczyty i okresy bez przypisywania CPC daty faktycznego pomiaru."""
    period = _data(event).get("period")
    start = str(period.get("start") or "") if isinstance(period, Mapping) else ""
    return _instant(event) or datetime.max.replace(tzinfo=UTC), start


def _counter(event: Mapping[str, Any], metric: str) -> Decimal | None:
    """Przyjmuje wyłącznie nieujemny całkowity stan licznika."""
    counters = _data(event).get("counters", {})
    value = _decimal(counters.get(metric)) if isinstance(counters, Mapping) else None
    return value if value is not None and value >= 0 and value == value.to_integral() else None


def counter_series(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Zwraca serie metric/source z punktami, przyrostami i numerami epok.

    Punkty zachowują observed_at/time_precision, event_id, period, value, delta,
    epoch, status i reason. Spadek, także między okresami, zatruwa serię do jawnego
    resetu; luka przerywa
    przyrost. Różne źródła nigdy nie są dodawane. Rozbieżne pomiary tej samej
    chwili i precyzji pozostają konfliktem, bez arbitralnego wyboru źródła.
    """
    event_list = list(events)
    groups: dict[tuple[str, str | None], list[Mapping[str, Any]]] = defaultdict(list)
    simultaneous: dict[tuple[str, datetime, str], set[Decimal]] = defaultdict(set)
    for event in event_list:
        if event.get("kind") == "billing_period":
            continue
        counters = _data(event).get("counters", {})
        for metric in METRICS:
            if isinstance(counters, Mapping) and metric in counters:
                groups[metric, _source(event)].append(event)
                value, instant = _counter(event, metric), _instant(event)
                if value is not None and instant is not None and _valid(event):
                    simultaneous[metric, *_time_key(event)].add(value)
    result = []
    for (metric, source), readings in sorted(
        groups.items(), key=lambda pair: (pair[0][0], pair[0][1] or "")
    ):
        markers = [
            event
            for event in event_list
            if _source(event) == source
            and (_flag(event, "reset", metric) or _flag(event, "gap", metric))
            and not (
                isinstance(_data(event).get("counters"), Mapping)
                and metric in _data(event)["counters"]
            )
        ]
        previous = None
        previous_event = None
        previous_period = None
        period_value = None
        epoch = 0
        conflicted = False
        points = []
        for event in sorted([*readings, *markers], key=_event_order):
            instant, value = _instant(event), _counter(event, metric)
            point = {
                "event_id": event.get("id"),
                "observed_at": _observed(event),
                "time_precision": _precision(event),
                "period": _data(event).get("period"),
                "value": _text_number(value),
                "delta": None,
                "epoch": epoch,
                "status": "known",
                "reason": "Odczyt źródłowy.",
            }
            if not _valid(event) or not source:
                status = "conflict" if event.get("status") == "conflict" else "unknown"
                conflicted = conflicted or status == "conflict"
                point.update(
                    status=status, reason="Niepotwierdzona jakość lub brak źródła odczytu."
                )
                previous = None
            elif instant is None:
                point.update(
                    status=(
                        "known" if value is not None and _precision(event) == "month" else "missing"
                    ),
                    reason="Brak daty faktycznego odczytu; bez przyrostu dziennego.",
                )
                period = _data(event).get("period")
                period_start = period.get("start") if isinstance(period, Mapping) else None
                if _precision(event) == "month" and value is not None and period_start:
                    if _flag(event, "reset", metric):
                        epoch += 1
                        conflicted, period_value = False, None
                        point.update(
                            epoch=epoch,
                            status="reset",
                            reason="Jawny reset: nowa epoka licznika okresowego.",
                        )
                    if period_value is not None and (
                        value < period_value
                        or period_start == previous_period
                        and value != period_value
                    ):
                        conflicted = True
                    if conflicted:
                        point.update(
                            status="conflict",
                            reason="Spadek lub rozbieżność licznika okresowego bez jawnego resetu.",
                        )
                    previous_period, period_value = period_start, value
            else:
                reset = _flag(event, "reset", metric)
                gap = _flag(event, "gap", metric)
                if reset:
                    epoch += 1
                    conflicted, previous = False, None
                    point.update(
                        epoch=epoch, status="reset", reason="Jawny reset: nowa epoka licznika."
                    )
                if gap:
                    previous = None
                    point.update(status="unknown", reason="Jawna luka w ciągłości danych.")
                if len(simultaneous[metric, *_time_key(event)]) > 1:
                    conflicted = True
                    point.update(
                        status="conflict", reason="Rozbieżne liczniki dla tej samej chwili."
                    )
                elif value is None and not reset and not gap:
                    point.update(status="missing", reason="Brak poprawnego stanu licznika.")
                elif value is not None:
                    if previous is not None and value < previous:
                        conflicted = True
                    if conflicted:
                        point.update(
                            status="conflict",
                            reason="Spadek lub konflikt licznika bez jawnego resetu.",
                        )
                    elif previous is not None and not gap and not reset:
                        same_day = (
                            previous_event is not None
                            and (
                                _precision(event) == "date" or _precision(previous_event) == "date"
                            )
                            and instant.date() == _instant(previous_event).date()
                        )
                        if not same_day:
                            with localcontext() as context:
                                context.prec = 80
                                point["delta"] = _text_number(value - previous)
                previous, previous_event = value, event
            points.append(point)
        statuses = {point["status"] for point in points}
        status = next(
            (status for status in ("conflict", "unknown", "missing") if status in statuses), "known"
        )
        result.append({"metric": metric, "source": source, "status": status, "points": points})
    return result


def _model(value: Any) -> str:
    """Ujednolica wielkość liter i odstępy bez utożsamiania różnych modeli."""
    return " ".join(str(value or "").upper().split())


def _compatible_model(entry: Mapping[str, Any], model: str, model_id: Any) -> bool:
    """Dopasowuje jawny identyfikator modelu albo pełną nazwę bez zgadywania rodziny."""
    models = entry.get("models", [entry.get("model")])
    if not isinstance(models, (list, tuple)):
        return False
    for candidate in models:
        if isinstance(candidate, Mapping):
            candidate_id = candidate.get("model_id", candidate.get("id"))
            if candidate_id is not None and model_id is not None:
                if str(candidate_id) == str(model_id):
                    return True
            elif candidate.get("model") and _model(candidate["model"]) == _model(model):
                return True
        elif isinstance(candidate, str) and _model(candidate) == _model(model):
            return True
        elif type(candidate) is int and model_id is not None and str(candidate) == str(model_id):
            return True
    return False


def _yield_for(
    model: str, color: str, item_id: Any, model_id: Any, yields: list[Mapping[str, Any]]
) -> tuple[Mapping[str, Any] | None, str]:
    """Wybiera jednoznaczną wydajność zgodnego modelu, koloru i kartoteki."""
    matches = [
        {**entry, "metric": entry.get("metric", "total" if color == "black" else "color")}
        for entry in yields
        if _compatible_model(entry, model, model_id)
        and entry.get("color") == color
        and (item_id is None or entry.get("item_id") == item_id)
        and entry.get("status") in {"confirmed", "estimated"}
        and entry.get("metric", "color" if color != "black" else "total")
        in ({"color"} if color != "black" else {"mono", "total"})
        and _decimal(entry.get("pages")) is not None
        and _decimal(entry["pages"]) > 0
        and _decimal(entry["pages"]) == _decimal(entry["pages"]).to_integral()
    ]
    confirmed = [entry for entry in matches if entry["status"] == "confirmed"]
    matches = confirmed or matches
    if not matches:
        return None, "Brak zgodnej wydajności modelu, koloru i licznika."
    if len({(_decimal(entry["pages"]), entry["metric"]) for entry in matches}) != 1:
        return None, "Konflikt wydajności zgodnych kartotek tonera."
    return matches[0], "Wydajność zgodna z modelem i kolorem."


def _replacement(event: Mapping[str, Any]) -> bool:
    """Uznaje wymianę tylko na podstawie jawnego potwierdzenia, nigdy alertu."""
    return event.get("kind") == "toner_replacement" and (
        _data(event).get("replacement_confirmed") is True or event.get("status") == "confirmed"
    )


def _toner_estimate(
    model: str,
    color: str,
    toner_events: list[Mapping[str, Any]],
    all_events: list[Mapping[str, Any]],
    yields: list[Mapping[str, Any]],
    series: list[dict[str, Any]],
) -> dict[str, Any]:
    """Wyznacza stan jednego koloru, zachowując pierwszeństwo dowodów."""
    result = {
        "model": model,
        "color": color,
        "status": "missing",
        "basis": None,
        "reason": "Brak pomiaru poziomu lub bazowego licznika cyklu tonera.",
        "remaining_percent": None,
        "remaining_pages": None,
        "consumed_pages": None,
        "yield_pages": None,
        "yield_status": None,
        "yield_source": None,
        "source": None,
        "observed_at": None,
        "baseline_at": None,
        "time_precision": None,
        "stock_quantity": "0",
    }
    timed = sorted([event for event in toner_events if _instant(event) is not None], key=_instant)
    measurements = [event for event in timed if event.get("kind") == "toner_level"]
    replacements = [event for event in timed if _replacement(event) and _valid(event)]
    shipments = [
        event
        for event in timed
        if event.get("kind") == "shipment"
        and _valid(event)
        and _decimal(_data(event).get("quantity")) is not None
        and _decimal(_data(event)["quantity"]) > 0
    ]
    measured = measurements[-1] if measurements else None
    replacement = replacements[-1] if replacements else None
    if measured is not None and (
        replacement is None or _instant(measured) >= _instant(replacement)
    ):
        baseline, basis = measured, "measured"
    elif replacement is not None:
        baseline, basis = replacement, "confirmedreplacement"
    elif shipments:
        baseline, basis = shipments[0], "shipmentbaseline"
    else:
        return result
    result.update(
        basis=basis,
        source=baseline.get("source"),
        observed_at=_observed(baseline),
        baseline_at=_observed(baseline),
        time_precision=_precision(baseline),
    )
    with localcontext() as context:
        context.prec = 80
        stock = sum(
            (
                _decimal(_data(event)["quantity"])
                for event in shipments
                if _instant(event) >= _instant(baseline)
            ),
            Decimal(0),
        )
        if basis == "shipmentbaseline":
            stock -= 1
        result["stock_quantity"] = _text_number(max(Decimal(0), stock))
    nominal, yield_reason = _yield_for(
        model, color, _data(baseline).get("item_id"), _data(baseline).get("model_id"), yields
    )
    if nominal is not None:
        result.update(
            yield_pages=_text_number(_decimal(nominal["pages"])),
            yield_status=nominal["status"],
            yield_source=nominal.get("source"),
        )
    if basis == "measured":
        level = _decimal(_data(baseline).get("level_percent"))
        competing = [event for event in measurements if _instant(event) == _instant(baseline)]
        if (
            not _valid(baseline)
            or not baseline.get("source")
            or level is None
            or not 0 <= level <= 100
        ):
            return {
                **result,
                "status": "unknown",
                "reason": "Niepoprawny lub niepotwierdzony pomiar poziomu tonera.",
            }
        if any(_decimal(_data(event).get("level_percent")) != level for event in competing):
            return {**result, "status": "unknown", "reason": "Konflikt pomiarów poziomu tonera."}
        metric = (
            nominal["metric"] if nominal is not None else "total" if color == "black" else "color"
        )
        if any(
            _source(event) == _source(baseline)
            and (_instant(event) is None or _instant(event) > _instant(baseline))
            and (_flag(event, "reset", metric) or _flag(event, "gap", metric))
            for event in all_events
        ):
            return {
                **result,
                "status": "unknown",
                "reason": "Luka lub reset po pomiarze; brak aktualnego poziomu tonera.",
            }
        result.update(
            status="measured",
            remaining_percent=_text_number(level),
            reason="Zmierzony poziom tonera; nie jest potwierdzeniem wymiany.",
        )
        if nominal is not None:
            with localcontext() as context:
                context.prec = 80
                result["remaining_pages"] = _text_number(_decimal(nominal["pages"]) * level / 100)
        else:
            result["reason"] += " " + yield_reason
        return result
    if nominal is None:
        return {**result, "reason": yield_reason}
    metric = nominal["metric"]
    start = _counter(baseline, metric)
    if start is None or not _source(baseline):
        return result
    matching = [
        entry
        for entry in series
        if entry["metric"] == metric and entry["source"] == _source(baseline)
    ]
    if not matching:
        return result
    points = [
        point
        for point in matching[0]["points"]
        if _instant(point) is not None and _instant(point) >= _instant(baseline)
    ]
    relevant_events = [
        event
        for event in all_events
        if _source(event) == _source(baseline)
        and (_instant(event) is None or _instant(event) > _instant(baseline))
    ]
    if any(
        _flag(event, "reset", metric) or _flag(event, "gap", metric) for event in relevant_events
    ) or any(point["status"] in {"conflict", "missing", "unknown"} for point in points):
        return {
            **result,
            "status": "unknown",
            "reason": "Luka, reset lub konflikt licznika przerywa cykl tonera.",
        }
    if not points:
        return result
    latest = points[-1]
    if any(
        _instant(event) is not None
        and _instant(event) > _instant(latest)
        and isinstance(_data(event).get("counters"), Mapping)
        and any(name in _data(event)["counters"] for name in METRICS)
        for event in relevant_events
    ):
        return {
            **result,
            "reason": f"Brak wymaganego licznika {metric} w najnowszym odczycie źródła.",
        }
    if _precision(baseline) == "date" or _precision(latest) == "date":
        if (
            _instant(latest).date() == _instant(baseline).date()
            and _decimal(latest["value"]) != start
        ):
            return {
                **result,
                "status": "unknown",
                "reason": "Precyzja daty nie rozstrzyga kolejności odczytu i początku cyklu.",
            }
    end = _decimal(latest["value"])
    if end is None or end < start:
        return {
            **result,
            "status": "unknown",
            "reason": "Brak ciągłego licznika od początku cyklu tonera.",
        }
    with localcontext() as context:
        context.prec = 80
        consumed = end - start
        remaining = max(Decimal(0), _decimal(nominal["pages"]) - consumed)
        percent = (remaining * 100 / _decimal(nominal["pages"])).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    return {
        **result,
        "status": "estimated",
        "reason": (
            "Szacunek od potwierdzonej wymiany."
            if basis == "confirmedreplacement"
            else "Szacunek od pierwszej wysyłki; dostawa nie potwierdza wymiany."
        ),
        "remaining_percent": _text_number(percent),
        "remaining_pages": _text_number(remaining),
        "consumed_pages": _text_number(consumed),
        "observed_at": latest["observed_at"],
        "time_precision": latest["time_precision"],
    }


def estimate_toners(
    events: Iterable[Mapping[str, Any]], yields: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Zwraca stan kolorów: pomiar, potwierdzona wymiana, pierwsza wysyłka.

    Wynik zawiera remaining_percent/pages, consumed_pages, yield_pages/status/source,
    status (measured/estimated/missing/unknown), basis, reason i pochodzenie czasu.
    ``stock_quantity`` oznacza potencjalny zapas wysyłek od podstawy, bez gwarancji
    aktualnego stanu magazynu odbiorcy. Ilość kolejnej wysyłki nie odnawia cyklu.
    Brak wymaganej metryki w podstawie lub nowszym odczycie daje missing; nie
    zastępujemy jej inną metryką ani sumą składowych. Pomiar poziomu jest niezależny.
    Nieznane modele/kolory nie są zgadywane; sam katalog nie tworzy faktu użycia.
    """
    event_list, catalog = list(events), list(yields)
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for event in event_list:
        data = _data(event)
        model = _model(data.get("model")) or (
            f"model_id:{data['model_id']}" if data.get("model_id") is not None else ""
        )
        if not model:
            continue
        if data.get("color") in COLORS:
            groups[model, data["color"]].append(event)
        levels = data.get("toner_levels")
        if isinstance(levels, Mapping) and event.get("kind") != "toner_level":
            for color in COLORS:
                if color in levels:
                    groups[model, color].append(
                        {
                            **event,
                            "kind": "toner_level",
                            "data": {**data, "color": color, "level_percent": levels[color]},
                        }
                    )
    series = counter_series(event_list)
    return [
        _toner_estimate(model, color, grouped, event_list, catalog, series)
        for (model, color), grouped in sorted(groups.items())
    ]


def build_narrative(
    device: Mapping[str, Any], summary: Mapping[str, Any], finance: Mapping[str, Any] | None = None
) -> str:
    """Buduje polski opis faktów bez dopowiadania wymian, kosztów i oszczędności.

    Urządzenie: ewidencja/model/serial. Podsumowanie: counters lub counter_series,
    toners (wynik estimate_toners), opcjonalnie supply_alert_count. Finanse przyjmują
    wynik purchase_cost albo słownik z takim wynikiem pod kluczem purchase_cost.
    """
    identity = [str(device[key]) for key in ("ewidencja", "model", "serial") if device.get(key)]
    sentences = ["Urządzenie: " + (", ".join(identity) or "brak identyfikacji") + "."]
    counters = summary.get("counters") or summary.get("counter_series") or []
    if counters:
        sources = sorted({str(entry["source"]) for entry in counters if entry.get("source")})
        sentences.append(
            "Liczniki zachowano osobno według metryki i źródła: " + ", ".join(sources) + "."
        )
        if any(entry.get("status") == "conflict" for entry in counters):
            sentences.append("Występują konflikty liczników; przyrosty przez konflikt są nieznane.")
        if any(
            point.get("time_precision") == "month"
            for entry in counters
            for point in entry.get("points", [])
        ):
            sentences.append("Dane miesięczne CPC nie stanowią odczytów dziennych.")
    else:
        sentences.append("Brak danych liczników.")
    color_labels = {"black": "czarny", "cyan": "cyjan", "magenta": "magenta", "yellow": "żółty"}
    for toner in summary.get("toners") or []:
        color = color_labels.get(toner.get("color"), "nieznany kolor")
        percent = _decimal(toner.get("remaining_percent"))
        if toner.get("status") == "measured" and percent is not None:
            sentences.append(f"Toner {color}: zmierzony poziom {_text_number(percent)}%.")
        elif toner.get("status") == "estimated" and percent is not None:
            basis = (
                "potwierdzonej wymiany"
                if toner.get("basis") == "confirmedreplacement"
                else "pierwszej wysyłki, która nie potwierdza wymiany"
            )
            sentences.append(
                f"Toner {color}: szacowany poziom {_text_number(percent)}% od {basis}."
            )
        else:
            sentences.append(
                f"Toner {color}: stan nieznany; {toner.get('reason') or 'brak wystarczających danych.'}"
            )
    if summary.get("supply_alert_count"):
        sentences.append("Zarejestrowano alerty materiałów; alert nie potwierdza wymiany tonera.")
    cost = finance.get("purchase_cost", finance) if finance is not None else None
    if isinstance(cost, Mapping):
        amount = _decimal(cost.get("amount"))
        if cost.get("status") == "known" and amount is not None and cost.get("currency") == "PLN":
            sentences.append(f"Koszt zakupu po zwrotach: {_money(amount)} PLN.")
        elif cost.get("status") == "planned":
            sentences.append("Koszt planowany: brak potwierdzonego wydania.")
        elif cost.get("status") == "excluded":
            sentences.append("Pozycja nie jest kosztem umowy.")
        else:
            sentences.append(
                "Koszt zakupu jest nieznany. "
                + str(cost.get("reason") or "Brak potwierdzonej historycznej ceny zakupu.")
            )
    else:
        sentences.append("Dane finansowe są prezentowane osobno zgodnie z uprawnieniami.")
    return " ".join(sentences)
