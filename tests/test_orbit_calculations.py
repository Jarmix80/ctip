"""Testy czystych kalkulacji ORBIT na syntetycznych faktach, bez usług i baz."""

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal, localcontext

import pytest

from app.services.orbit_calculations import (
    build_narrative,
    counter_series,
    estimate_toners,
    purchase_cost,
)


def purchase(**values):
    """Buduje historyczną pozycję umowy z różnymi cenami zakupu i sprzedaży."""
    return {
        "role": "contract",
        "purchase_price": "100",
        "sale_price": "180",
        "issued_quantity": "2",
        **values,
    }


def event(day=1, kind="counter_reading", source="telemetry", **data):
    """Tworzy datowane zdarzenie jednego urządzenia ze znanym pochodzeniem."""
    return {
        "id": f"{kind}:{source}:{day}",
        "kind": kind,
        "observed_at": f"2026-09-{day:02d}T12:00:00Z",
        "time_precision": "datetime",
        "status": "known",
        "source": source,
        "data": data,
    }


def toner(day=1, kind="shipment", **data):
    """Tworzy wysyłkę lub fakt tonera zgodnego z katalogiem testowym."""
    return event(day, kind, model_id=97, model="IM C3000", color="black", item_id=11, **data)


def catalog(**values):
    """Zwraca katalog w kontrakcie adaptera, bez wymogu dodatkowego źródła."""
    return [
        {
            "item_id": 11,
            "models": [{"id": 97, "model": "IM C3000"}],
            "color": "black",
            "pages": 1000,
            "status": "confirmed",
            **values,
        }
    ]


@pytest.mark.parametrize(
    ("values", "amount", "quantity"),
    [
        ({}, "200.00", "2"),
        ({"returned_quantity": "1"}, "100.00", "1"),
        ({"returned_quantity": "2"}, "0.00", "0"),
        ({"purchase_price": Decimal("0.105"), "issued_quantity": Decimal("3")}, "0.32", "3"),
        (
            {"purchase_price": "100.0001", "issued_quantity": "1.5", "returned_quantity": ".25"},
            "125.00",
            "1.25",
        ),
    ],
)
def test_purchase_cost_uses_historical_purchase_and_net_issues(values, amount, quantity):
    """Cena sprzedaży nie wpływa na koszt; zwroty pomniejszają wydania."""
    result = purchase_cost(purchase(**values))
    assert result == {
        "amount": amount,
        "status": "known",
        "reason": "Historyczna cena zakupu pomnożona przez ilość wydaną po zwrotach.",
        "currency": "PLN",
        "quantity": quantity,
    }


@pytest.mark.parametrize("price", [None, "", "NaN", "Infinity", "abc", True, "0"])
def test_purchase_cost_unknown_price_never_uses_sale_or_document(price):
    """Brak ceny i techniczne zero pozostawiają nieznany koszt."""
    result = purchase_cost(purchase(purchase_price=price))
    assert result["status"] == "unknown"
    assert result["amount"] is None
    if price is None:
        assert (
            purchase_cost(purchase(purchase_price=None, document_purchase_price="100"))["amount"]
            is None
        )


@pytest.mark.parametrize(
    "flag,status", [(True, "known"), (False, "unknown"), ("true", "unknown"), (1, "unknown")]
)
def test_purchase_cost_requires_explicit_confirmation_for_zero(flag, status):
    """Wyłącznie logiczne True potwierdza rzeczywisty zerowy koszt."""
    result = purchase_cost(purchase(purchase_price="0", zero_cost_confirmed=flag))
    assert result["status"] == status
    assert result["amount"] == ("0.00" if flag is True else None)


def test_purchase_cost_no_issue_is_planned_without_inventing_zero():
    """Planowana ilość nie stanowi faktycznego kosztu umowy."""
    result = purchase_cost(purchase(issued_quantity="0", quantity="8", purchase_price=None))
    assert result["status"] == "planned"
    assert result["amount"] is None
    assert result["quantity"] == "0"


@pytest.mark.parametrize("role", [None, "sale", "service", "internal", ""])
def test_purchase_cost_excludes_noncontract_positions(role):
    """Obce role nie trafiają do kosztów umowy."""
    assert purchase_cost(purchase(role=role))["status"] == "excluded"
    assert purchase_cost(purchase(role=role))["amount"] is None


@pytest.mark.parametrize(
    "values",
    [
        {"document_purchase_price": "101"},
        {"purchase_value": "180"},
        {"returned_quantity": "3"},
        {"issued_quantity": "-1"},
        {"returned_quantity": "-1"},
        {"purchase_price": "-1"},
        {"purchase_price": "0", "document_purchase_price": "100"},
    ],
)
def test_purchase_cost_conflicts_leave_amount_unknown(values):
    """Niespójność dokumentów lub ilości blokuje wyliczenie kwoty."""
    result = purchase_cost(purchase(**values))
    assert result["status"] == "conflict"
    assert result["amount"] is None


def test_purchase_cost_reconciles_gross_issue_value_before_returns():
    """Wartość zakupu dotyczy pierwotnej ilości, a koszt uwzględnia zwrot."""
    assert (
        purchase_cost(
            purchase(purchase_value="200", document_purchase_price="100", returned_quantity="1")
        )["amount"]
        == "100.00"
    )


@pytest.mark.parametrize(
    "values",
    [
        {"currency": "EUR"},
        {"issued_quantity": None},
        {"returned_quantity": None},
        {"purchase_value": "bad"},
    ],
)
def test_purchase_cost_rejects_missing_quantities_and_foreign_currency(values):
    """Nieznane dane nie są zastępowane zerem ani kursem wymiany."""
    assert purchase_cost(purchase(**values))["status"] == "unknown"


def test_purchase_cost_decimal_arithmetic_is_independent_of_caller_precision():
    """Lokalny kontekst chroni grosze i duże ilości przed ustawieniami wywołującego."""
    with localcontext() as context:
        context.prec = 3
        assert (
            purchase_cost(purchase(purchase_price="123456789.1234", issued_quantity="100"))[
                "amount"
            ]
            == "12345678912.34"
        )
        assert context.prec == 3


def test_counter_series_separates_metrics_sources_and_orders_points():
    """Przyrosty liczone są w obrębie jednej metryki i jednego źródła."""
    result = counter_series(
        [
            event(3, counters={"mono": 120, "color": 30, "scans": 9, "total": 150}),
            event(2, source="other", counters={"mono": 800}),
            event(1, counters={"mono": 100, "color": 20, "scans": 5, "total": 120}),
        ]
    )
    series = {(entry["metric"], entry["source"]): entry for entry in result}
    assert len(series) == 5
    assert series["mono", "telemetry"]["points"][-1]["delta"] == "20"
    assert series["total", "telemetry"]["points"][-1]["delta"] == "30"
    assert series["scans", "telemetry"]["points"][-1]["delta"] == "4"
    assert series["mono", "other"]["points"][0]["delta"] is None


def test_counter_decrease_remains_conflicted_until_explicit_reset():
    """Odbicie wartości po spadku nie stanowi samodzielnego potwierdzenia resetu."""
    result = counter_series(
        [
            event(1, counters={"total": 100}),
            event(2, counters={"total": 20}),
            event(3, counters={"total": 130}),
            event(4, counters={"total": 5}, reset=True),
            event(5, counters={"total": 15}),
        ]
    )[0]
    assert [point["status"] for point in result["points"]] == [
        "known",
        "conflict",
        "conflict",
        "reset",
        "known",
    ]
    assert [point["delta"] for point in result["points"]] == [None, None, None, None, "10"]
    assert [point["epoch"] for point in result["points"]] == [0, 0, 0, 1, 1]


def test_counter_reset_marker_without_value_starts_epoch():
    """Osobny fakt resetu działa także bez jednoczesnego odczytu."""
    points = counter_series(
        [
            event(1, counters={"total": 100}),
            event(2, "counter_reset"),
            event(3, counters={"total": 3}),
            event(4, counters={"total": 5}),
        ]
    )[0]["points"]
    assert points[2]["epoch"] == 1
    assert points[2]["delta"] is None
    assert points[3]["delta"] == "2"


def test_counter_reset_only_affects_selected_metric():
    """Reset jednego licznika nie maskuje konfliktu drugiego."""
    result = {
        entry["metric"]: entry
        for entry in counter_series(
            [
                event(1, counters={"mono": 100, "color": 50}),
                event(2, counters={"mono": 1, "color": 5}, reset=["mono"]),
            ]
        )
    }
    assert result["mono"]["points"][-1]["status"] == "reset"
    assert result["color"]["points"][-1]["status"] == "conflict"


def test_counter_conflicting_sources_at_same_instant_are_not_selected_or_summed():
    """Oba rozbieżne źródła zachowują odczyt i jawny konflikt."""
    result = counter_series(
        [
            event(1, source="first", counters={"total": 100}),
            event(1, source="second", counters={"total": 150}),
        ]
    )
    assert len(result) == 2
    assert {entry["status"] for entry in result} == {"conflict"}
    assert {entry["points"][0]["value"] for entry in result} == {"100", "150"}
    assert all(entry["points"][0]["delta"] is None for entry in result)


def test_counter_agreeing_duplicate_is_not_double_counted():
    """Powtórzony zgodny fakt nie zwiększa przyrostu."""
    first = event(1, counters={"total": 100})
    result = counter_series([first, deepcopy(first), event(2, counters={"total": 110})])[0]
    assert result["status"] == "known"
    assert sum(Decimal(point["delta"] or "0") for point in result["points"]) == 10


def test_counter_cpc_billing_is_not_a_daily_counter_series():
    """Miesięczne granice rozliczeń nie tworzą obserwacji dziennych."""
    monthly = {
        **event(
            1,
            "billing_period",
            "ms_cpc",
            billing={"start": "2026-09-01", "end": "2026-09-30"},
            counters={"total": 100},
        ),
        "observed_at": None,
        "time_precision": "month",
    }
    assert counter_series([monthly]) == []
    legacy = {**monthly, "kind": "counter_reading"}
    point = counter_series([legacy])[0]["points"][0]
    assert point["observed_at"] is None
    assert point["time_precision"] == "month"
    assert point["delta"] is None


@pytest.mark.parametrize("value", ["NaN", "Infinity", True, "-1", "1.5", None])
def test_counter_invalid_value_breaks_increment(value):
    """Niepoprawny odczyt nie pozwala przeskakiwać przez lukę."""
    points = counter_series(
        [
            event(1, counters={"total": 100}),
            event(2, counters={"total": value}),
            event(3, counters={"total": 110}),
        ]
    )[0]["points"]
    assert points[1]["status"] == "missing"
    assert points[2]["delta"] is None


def test_counter_timezone_offsets_refer_to_same_instant():
    """Różne zapisy strefy czasowej nie ukrywają jednoczesnego konfliktu."""
    first = {
        **event(1, counters={"total": 100}),
        "observed_at": datetime(2026, 9, 1, 12, tzinfo=UTC),
    }
    second = {
        **event(1, source="other", counters={"total": 101}),
        "observed_at": "2026-09-01T14:00:00+02:00",
    }
    assert all(entry["status"] == "conflict" for entry in counter_series([first, second]))


def test_toners_shipment_baseline_does_not_reset_with_next_shipment():
    """Kolejna dostawa i wiele sztuk oznaczają potencjalny zapas, nie nowy cykl."""
    events = [
        toner(1, quantity=2, counters={"total": 100}),
        toner(2, quantity=3, counters={"total": 300}),
        event(3, counters={"total": 400}),
    ]
    result = estimate_toners(events, catalog())[0]
    assert result["basis"] == "shipmentbaseline"
    assert result["status"] == "estimated"
    assert result["remaining_percent"] == "70"
    assert result["remaining_pages"] == "700"
    assert result["consumed_pages"] == "300"
    assert result["stock_quantity"] == "4"
    assert result["baseline_at"] == events[0]["observed_at"]


def test_toners_confirmed_replacement_has_priority_over_shipments():
    """Potwierdzona wymiana ustala nowy początek cyklu."""
    result = estimate_toners(
        [
            toner(1, quantity=1, counters={"total": 100}),
            toner(2, "toner_replacement", replacement_confirmed=True, counters={"total": 500}),
            toner(3, quantity=2, counters={"total": 600}),
            event(4, counters={"total": 700}),
        ],
        catalog(),
    )[0]
    assert result["basis"] == "confirmedreplacement"
    assert result["remaining_percent"] == "80"


def test_toners_measured_levels_have_priority_and_use_model_catalog():
    """Słownik toner_levels daje pomiar każdego koloru zgodnie z katalogiem."""
    events = [
        toner(1, quantity=1, counters={"total": 100}),
        event(
            2,
            "daily_snapshot",
            model_id=97,
            model="IM C3000",
            toner_levels={"black": 37, "cyan": 50},
            counters={"total": 500},
        ),
    ]
    result = {
        entry["color"]: entry
        for entry in estimate_toners(
            events, catalog() + catalog(item_id=12, color="cyan", pages=2000)
        )
    }
    assert result["black"]["basis"] == "measured"
    assert result["black"]["remaining_percent"] == "37"
    assert result["black"]["remaining_pages"] == "370"
    assert result["cyan"]["remaining_pages"] == "1000"


def test_toners_replacement_after_measurement_supersedes_old_cartridge():
    """Pomiar poprzedniego wkładu nie przesłania późniejszej potwierdzonej wymiany."""
    result = estimate_toners(
        [
            toner(1, "toner_level", level_percent=5),
            toner(2, "toner_replacement", replacement_confirmed=True, counters={"total": 100}),
            event(3, counters={"total": 200}),
        ],
        catalog(),
    )[0]
    assert result["basis"] == "confirmedreplacement"
    assert result["remaining_percent"] == "90"


def test_toners_supply_alert_is_not_a_replacement():
    """Nawet flaga potwierdzenia w alercie nie czyni go wymianą."""
    result = estimate_toners(
        [
            toner(1, quantity=1, counters={"total": 100}),
            toner(2, "supply_alert", replacement_confirmed=True, counters={"total": 800}),
            event(3, counters={"total": 900}),
        ],
        catalog(),
    )[0]
    assert result["basis"] == "shipmentbaseline"
    assert result["remaining_percent"] == "20"
    assert (
        estimate_toners([toner(1, "supply_alert", counters={"total": 50})], catalog())[0]["status"]
        == "missing"
    )


def test_toners_unconfirmed_replacement_is_not_a_baseline():
    """Nazwa typu zdarzenia bez dowodu nie potwierdza fizycznej wymiany."""
    assert (
        estimate_toners([toner(1, "toner_replacement", counters={"total": 0})], catalog())[0][
            "status"
        ]
        == "missing"
    )


@pytest.mark.parametrize(
    "values",
    [
        {"models": [{"id": 98, "model": "IM C3000"}]},
        {"models": ["IM C3500"]},
        {"color": "cyan"},
        {"item_id": 12},
        {"pages": 0},
        {"status": "missing"},
        {"metric": "scans"},
    ],
)
def test_toners_yield_requires_model_color_item_and_usable_pages(values):
    """Niezgodna wydajność nie może zostać użyta do szacowania cyklu."""
    result = estimate_toners(
        [toner(1, quantity=1, counters={"total": 100}), event(2, counters={"total": 200})],
        catalog(**values),
    )[0]
    assert result["status"] == "missing"
    assert result["remaining_percent"] is None


def test_toners_first_shipment_without_counter_cannot_be_replaced_by_second():
    """Dostawa zapasu nie naprawia brakującej podstawy pierwszego cyklu."""
    result = estimate_toners(
        [
            toner(1, quantity=1),
            toner(2, quantity=2, counters={"total": 200}),
            event(3, counters={"total": 300}),
        ],
        catalog(),
    )[0]
    assert result["status"] == "missing"
    assert result["remaining_percent"] is None


@pytest.mark.parametrize(
    "kind,values",
    [
        ("counter_reset", {}),
        ("telemetry_gap", {}),
        ("counter_reading", {"reset": True, "counters": {"total": 0}}),
        ("counter_reading", {"counters": {"total": 20}}),
        ("counter_reading", {"gap": True, "counters": {"total": 110}}),
    ],
)
def test_toners_reset_gap_and_decrease_make_cycle_unknown(kind, values):
    """Po przerwaniu ciągłości liczników nie publikuje się procentowego szacunku."""
    result = estimate_toners(
        [
            toner(1, quantity=1, counters={"total": 100}),
            event(2, kind, **values),
            event(3, counters={"total": 300}),
        ],
        catalog(),
    )[0]
    assert result["status"] == "unknown"
    assert result["remaining_percent"] is None


def test_toners_do_not_combine_different_counter_sources():
    """Obcy późniejszy licznik nie stanowi końca cyklu źródła podstawowego."""
    result = estimate_toners(
        [
            toner(1, quantity=1, counters={"total": 100}),
            event(2, source="other", counters={"total": 900}),
        ],
        catalog(),
    )[0]
    assert result["consumed_pages"] == "0"
    assert result["observed_at"] == "2026-09-01T12:00:00Z"


def test_toners_counter_source_can_be_provided_for_shipping_document():
    """Dokument dostawy wskazuje jawnie pochodzenie przypisanego licznika."""
    first = {
        **toner(1, quantity=1, counter_source="telemetry", counters={"total": 100}),
        "source": "shipping",
    }
    result = estimate_toners([first, event(2, counters={"total": 200})], catalog())[0]
    assert result["consumed_pages"] == "100"
    assert result["source"] == "shipping"


def test_toners_conflicting_yields_are_not_arbitrarily_selected():
    """Rozbieżne zgodne wydajności nie dają pozornej precyzji."""
    result = estimate_toners(
        [toner(1, quantity=1, counters={"total": 100})], catalog() + catalog(pages=2000)
    )[0]
    assert result["remaining_percent"] is None
    assert "Konflikt" in result["reason"]


def test_toners_measured_percent_does_not_require_nominal_yield():
    """Brak katalogu nie usuwa bezpośredniego pomiaru poziomu."""
    result = estimate_toners([toner(1, "toner_level", level_percent="12.5")], [])[0]
    assert result["status"] == "measured"
    assert result["remaining_percent"] == "12.5"
    assert result["remaining_pages"] is None


@pytest.mark.parametrize("level", [-1, 101, None, "NaN", True])
def test_toners_invalid_measurement_does_not_fall_back_to_shipments(level):
    """Błędny aktualny pomiar nie jest zastępowany wygodnym szacunkiem wysyłkowym."""
    result = estimate_toners(
        [
            toner(1, quantity=1, counters={"total": 100}),
            toner(2, "toner_level", level_percent=level),
        ],
        catalog(),
    )[0]
    assert result["status"] == "unknown"


def test_toners_exhaustion_is_clamped_and_does_not_roll_over():
    """Przekroczenie wydajności nie uruchamia kolejnego domniemanego wkładu."""
    result = estimate_toners(
        [toner(1, quantity=5, counters={"total": 100}), event(2, counters={"total": 3000})],
        catalog(),
    )[0]
    assert result["remaining_percent"] == "0"
    assert result["remaining_pages"] == "0"


@pytest.mark.parametrize(
    ("metric", "consumed", "remaining"),
    [("total", "400", "60"), ("mono", "100", "90")],
)
def test_toners_black_uses_catalog_metric_including_color_pages(metric, consumed, remaining):
    """Jawne total obejmuje kolor; jawne mono pozostaje decyzją adaptera katalogu."""
    events = [
        toner(1, quantity=1, counters={"mono": 100, "color": 200, "total": 300}),
        event(2, counters={"mono": 200, "color": 500, "total": 700}),
    ]
    result = estimate_toners(events, catalog(metric=metric))[0]
    assert result["status"] == "estimated"
    assert result["consumed_pages"] == consumed
    assert result["remaining_percent"] == remaining
    if metric == "total":
        assert estimate_toners(events, catalog())[0]["remaining_percent"] == remaining


@pytest.mark.parametrize("missing_at", ["baseline", "latest", "both"])
def test_toners_black_total_missing_never_falls_back_to_mono_or_sum(missing_at):
    """Brak wymaganego total nie uruchamia zastępstwa mono ani sumowania składowych."""
    baseline = {"mono": 100, "color": 200, "total": 300}
    latest = {"mono": 200, "color": 500, "total": 700}
    if missing_at in {"baseline", "both"}:
        baseline.pop("total")
    if missing_at in {"latest", "both"}:
        latest.pop("total")
    result = estimate_toners(
        [toner(1, quantity=1, counters=baseline), event(2, counters=latest)],
        catalog(metric="total"),
    )[0]
    assert result["status"] == "missing"
    assert result["remaining_percent"] is None
    assert result["consumed_pages"] is None


def test_toners_color_uses_color_counter_without_summing_scans():
    """Kolorowy wkład korzysta z licznika koloru niezależnie od skanów i mono."""
    first = toner(1, quantity=1, counters={"color": 100, "total": 1000, "scans": 500})
    first["data"]["color"] = "cyan"
    result = estimate_toners(
        [first, event(2, counters={"color": 300, "total": 3000, "scans": 900})],
        catalog(color="cyan"),
    )[0]
    assert result["remaining_percent"] == "80"


def test_narrative_distinguishes_costs_measurements_estimates_and_alerts():
    """Opis nie zamienia szacunku, alertu ani ceny sprzedaży w potwierdzony fakt."""
    events = [toner(1, quantity=1, counters={"total": 100}), event(2, counters={"total": 300})]
    summary = {
        "counters": counter_series(events),
        "toners": estimate_toners(events, catalog()),
        "supply_alert_count": 2,
    }
    text = build_narrative(
        {"ewidencja": "KP/123", "model": "IM C3000", "serial": "TEST"},
        summary,
        purchase_cost(purchase()),
    )
    assert "KP/123" in text
    assert "200.00 PLN" in text
    assert "szacowany poziom 80%" in text
    assert "wysyłki, która nie potwierdza wymiany" in text
    assert "alert nie potwierdza wymiany" in text
    assert "180" not in text


def test_narrative_unknown_cost_is_not_zero():
    """Brak ceny jest opisany jako nieznany koszt, bez sugerowania oszczędności."""
    text = build_narrative({}, {}, {"purchase_cost": purchase_cost(purchase(purchase_price=None))})
    assert "Koszt zakupu jest nieznany" in text
    assert "0.00 PLN" not in text


def test_calculations_are_pure_and_accept_iterables():
    """Wszystkie funkcje zachowują dane wejściowe i nie wymagają list iteratorów."""
    events = [toner(1, quantity=1, counters={"total": 100}), event(2, counters={"total": 300})]
    yields = catalog()
    costs = purchase()
    snapshot = deepcopy((events, yields, costs))
    summary = {
        "counters": counter_series(iter(events)),
        "toners": estimate_toners(iter(events), iter(yields)),
    }
    summary_snapshot = deepcopy(summary)
    build_narrative({}, summary, purchase_cost(costs))
    assert (events, yields, costs) == snapshot
    assert summary == summary_snapshot
    assert counter_series([]) == []
    assert estimate_toners([], yields) == []


def test_narrative_omitted_finance_does_not_claim_missing_data():
    """Pominięta część finansowa oznacza osobną prezentację według uprawnień."""
    text = build_narrative({}, {})
    assert "Dane finansowe są prezentowane osobno zgodnie z uprawnieniami." in text
    assert "Brak danych kosztu" not in text


def test_toners_expanded_adapter_events_preserve_individual_colors():
    """Pomiar rozwinięty przez adapter nie jest ponownie rozdzielany na kolory."""
    first = toner(1, "toner_level", level_percent=20, toner_levels={"black": 20, "cyan": 80})
    second = deepcopy(first)
    second["data"].update(color="cyan", level_percent=80, item_id=12)
    values = {
        entry["color"]: entry
        for entry in estimate_toners([first, second], catalog() + catalog(item_id=12, color="cyan"))
    }
    assert values["black"]["remaining_percent"] == "20"
    assert values["cyan"]["remaining_percent"] == "80"
    assert values["black"]["remaining_pages"] == "200"
    assert values["cyan"]["remaining_pages"] == "800"


def test_counter_supports_second_precision_and_counter_reset_alias():
    """Sekundowy czas adaptera zachowuje konflikty, a jego reset rozpoczyna epokę."""
    first = event(1, counters={"total": 100})
    conflict = {**event(1, source="other", counters={"total": 200}), "time_precision": "second"}
    reset = event(2, counters={"total": 5}, counter_reset=True)
    result = {entry["source"]: entry for entry in counter_series([first, conflict, reset])}
    assert result["other"]["points"][0]["status"] == "conflict"
    assert result["telemetry"]["points"][1]["status"] == "reset"
    assert result["telemetry"]["points"][1]["epoch"] == 1


def test_counter_preserves_explicit_source_conflict():
    """Jawny konflikt źródła nie jest obniżany do zwykłego braku danych."""
    conflict = {**event(2, counters={"total": 110}), "status": "conflict"}
    result = counter_series(
        [event(1, counters={"total": 100}), conflict, event(3, counters={"total": 120})]
    )[0]
    assert result["status"] == "conflict"
    assert result["points"][-1]["status"] == "conflict"
    assert result["points"][-1]["delta"] is None


def test_toners_undated_gap_invalidates_a_shipment_estimate():
    """Luka bez daty nie może być arbitralnie odsunięta poza szacowany cykl."""
    gap = {**event(2, "unavailable"), "observed_at": None}
    result = estimate_toners(
        [toner(1, quantity=1, counters={"total": 100}), gap, event(3, counters={"total": 300})],
        catalog(),
    )[0]
    assert result["status"] == "unknown"
    assert result["remaining_percent"] is None


def test_toners_same_day_without_time_does_not_order_baseline_and_reading():
    """Data dzienna nie dowodzi kolejności pomiaru i pierwszej dostawy."""
    first = {
        **toner(1, quantity=1, counters={"total": 100}),
        "time_precision": "date",
        "observed_at": "2026-09-01",
    }
    result = estimate_toners([first, event(1, counters={"total": 200})], catalog())[0]
    assert result["status"] == "unknown"
    assert result["remaining_percent"] is None


def test_toners_unknown_model_id_matches_only_explicit_name_compatibility():
    """Katalog z nazwami modeli może potwierdzić zgodność bez technicznego ID."""
    result = estimate_toners(
        [toner(1, quantity=1, counters={"total": 100}), event(2, counters={"total": 200})],
        catalog(models=[{"model": "IM C3000"}]),
    )[0]
    assert result["status"] == "estimated"
    assert result["remaining_percent"] == "90"


def test_toners_new_measurement_restores_known_level_after_gap():
    """Nowy pomiar kończy niepewność po luce; pomiar sprzed luki jej nie maskuje."""
    events = [toner(1, "toner_level", level_percent=90), event(2, "unavailable")]
    assert estimate_toners(events, catalog())[0]["status"] == "unknown"
    events.append(toner(3, "toner_level", level_percent=60))
    result = estimate_toners(events, catalog())[0]
    assert result["status"] == "measured"
    assert result["remaining_percent"] == "60"


def test_counter_cpc_only_report_retains_monthly_series_without_daily_deltas():
    """Adapter CPC zapewnia pokrycie okresowe bez sztucznej świeżości dziennej."""
    august = {
        **event(
            1,
            "billing_counter",
            "ms_cpc",
            counters={"mono": 100, "color": 10},
            period={"start": "2026-08-01", "end": "2026-08-31"},
        ),
        "observed_at": None,
        "time_precision": "month",
        "status": "confirmed",
    }
    september = deepcopy(august)
    september["id"] = "cpc:september"
    september["data"].update(
        counters={"mono": 250, "color": 30}, period={"start": "2026-09-01", "end": "2026-09-30"}
    )
    series = counter_series([september, august])
    assert len(series) == 2
    assert {entry["source"] for entry in series} == {"ms_cpc"}
    assert {entry["status"] for entry in series} == {"known"}
    for entry in series:
        assert [point["period"]["start"] for point in entry["points"]] == [
            "2026-08-01",
            "2026-09-01",
        ]
        assert all(point["observed_at"] is None for point in entry["points"])
        assert all(point["time_precision"] == "month" for point in entry["points"])
        assert all(point["delta"] is None for point in entry["points"])
    assert "Dane miesięczne CPC nie stanowią odczytów dziennych." in build_narrative(
        {}, {"counters": series}
    )
    assert (
        estimate_toners([toner(1, quantity=1), august, september], catalog())[0]["status"]
        == "missing"
    )


def test_counter_monthly_decrease_is_conflict_without_inventing_daily_delta():
    """Miesięczna precyzja nie maskuje spadku ani nie tworzy przyrostu dziennego."""
    first = {
        **event(
            1,
            "billing_counter",
            "ms_cpc",
            counters={"mono": 100},
            period={"start": "2026-07-01", "end": "2026-07-31"},
        ),
        "observed_at": None,
        "time_precision": "month",
    }
    second = deepcopy(first)
    second["data"].update(
        counters={"mono": 20}, period={"start": "2026-08-01", "end": "2026-08-31"}
    )
    third = deepcopy(second)
    third["data"].update(
        counters={"mono": 5},
        period={"start": "2026-09-01", "end": "2026-09-30"},
        counter_reset=True,
    )
    points = counter_series([third, second, first])[0]["points"]
    assert [point["status"] for point in points] == ["known", "conflict", "reset"]
    assert all(point["delta"] is None for point in points)
    assert points[-1]["epoch"] == 1
