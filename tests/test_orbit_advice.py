"""Regresje prognoz, bezpiecznych ostrzeżeń i wieloźródłowych przesyłek."""

import calendar
from datetime import date

import pytest

from app.api.routes.admin_orbit import PolicyValues
from app.services.orbit_advice import DEFAULT_POLICY, assessment, forecasts, toner_forecasts
from app.services.orbit_linkage import consolidate
from scripts.windows.provision_orbit_reader import grant_statements


def periods(months=(6, 7, 8), *, pages=300):
    """Buduje pełne okresy z rozłącznymi metrykami bez baz źródłowych."""
    result = []
    for month in months:
        result.append(
            {
                "id": str(month),
                "kind": "billing_period",
                "contract_id": "7",
                "status": "confirmed",
                "data": {
                    "period_start": f"2026-{month:02}-01",
                    "period_end": f"2026-{month:02}-{calendar.monthrange(2026, month)[1]}",
                    "billing": {"mono": pages, "color": pages / 2},
                },
            }
        )
    return result


def test_toner_forecast_preserves_measurement_and_respects_expiry():
    """Tempo CPC daje wyłącznie szacunek czasu, a starego poziomu nie odmładza."""
    today = date(2026, 9, 13)
    forecast = [{"metric": "color", "status": "estimated", "daily_pages": "100"}]
    measured = {
        "color": "cyan",
        "remaining_pages": "1000",
        "remaining_percent": "50",
        "observed_at": "2026-09-12",
    }
    result = toner_forecasts([measured], forecast, [], today=today)[0]
    assert result["remaining_days"] == "9.0"
    assert result["remaining_percent"] == "50"
    stale = {**measured, "observed_at": "2026-09-08"}
    assert toner_forecasts([stale], forecast, [], today=today)[0]["remaining_days"] is None
    assert (
        toner_forecasts([stale], forecast, [], today=today, measurement_stale_days=7)[0][
            "remaining_days"
        ]
        == "5.0"
    )


def test_lead_time_allows_toner_needed_before_delivery_without_spares():
    """Zapas zero nie ostrzega, jeśli toner skończy się przed planowanym dostarczeniem."""
    measured = {
        "color": "cyan",
        "status": "measured",
        "remaining_percent": "30",
        "remaining_days": "4",
        "observed_at": "2026-09-13",
    }
    assert assessment([measured], {}, {"cyan": 1}, today=date(2026, 9, 13))["status"] == "ok"
    assert (
        assessment([measured], {"cyan": {"lead_days": 2}}, {"cyan": 1}, today=date(2026, 9, 13))[
            "status"
        ]
        == "warning"
    )


def test_forecast_separates_billing_and_projection():
    """Trzy miesiące dają tempo ważone liczbą dni, nie średnią miesięcznych kwot."""
    result = forecasts(periods(), today=date(2026, 9, 13))[0]
    assert result["status"] == "estimated"
    assert result["daily_pages"] == "9.78"
    assert result["since_period_pages"] == "127"
    assert result["next_30_days_pages"] == "293"
    assert "remaining_percent" not in result


@pytest.mark.parametrize("months", [(6, 8), (5, 7, 8), (7, 8)])
def test_missing_period_prevents_forecast(months):
    """Nie interpoluje brakującego miesiąca ani krótkiej historii."""
    assert forecasts(periods(months), today=date(2026, 9, 13))[0]["status"] == "insufficient_data"


def test_stale_contract_conflict_and_zero():
    """Starość, zmiana umowy i konflikt nie są prognozą; prawdziwe zero nią jest."""
    assert forecasts(periods(), today=date(2026, 11, 1))[0]["status"] == "stale"
    data = periods()
    data[-1]["contract_id"] = "8"
    assert forecasts(data, today=date(2026, 9, 13))[0]["status"] == "insufficient_data"
    data[-1]["status"] = "conflict"
    assert forecasts(data, today=date(2026, 9, 13))[0]["status"] == "insufficient_data"
    assert forecasts(periods(pages=0), today=date(2026, 9, 13))[0]["next_30_days_pages"] == "0"


def test_duplicate_month_never_counts_twice():
    """Dwa rozliczenia tego samego okresu wymagają uzgodnienia."""
    data = periods()
    assert forecasts([*data, data[-1]], today=date(2026, 9, 13))[0]["status"] == "insufficient_data"


def toner(level=80, observed="2026-09-12"):
    """Świeży pomiar nie zakłada rzeczywistego zapasu w magazynie odbiorcy."""
    return [
        {
            "color": "black",
            "status": "measured",
            "basis": "measured",
            "remaining_percent": level,
            "observed_at": observed,
            "stock_quantity": "0",
        }
    ]


def test_zero_spare_warning_does_not_block():
    """Wczesna dostawa przy zapasie zero daje wyłącznie wskazówkę."""
    result = assessment(toner(), {}, {"black": 1}, today=date(2026, 9, 13))
    assert DEFAULT_POLICY["spare_toners"] == 0
    assert result["status"] == "warning"
    assert result["blocking"] is False


def test_low_level_exception_pending_and_stale():
    """Wyjątki, transport w toku i stare pomiary zachowują różne znaczenia."""
    today = date(2026, 9, 13)
    assert assessment(toner(10), {}, {"black": 1}, today=today)["status"] == "ok"
    assert (
        assessment(toner(), {"black": {"spare_toners": 1}}, {"black": 1}, today=today)["status"]
        == "ok"
    )
    assert assessment(toner(10), {}, {"black": 2}, today=today)["status"] == "warning"
    assert assessment(toner(10), {}, {"black": 1}, {"black": 1}, today=today)["status"] == "warning"
    assert (
        assessment(toner(observed="2026-07-01"), {}, {"black": 1}, today=today)["status"]
        == "insufficient_data"
    )


@pytest.mark.parametrize(
    "values",
    [
        {"spare_toners": -1},
        {"low_percent": 101},
        {"lead_days": 0},
        {"spare_toners": True},
        {"secret": 1},
    ],
)
def test_policy_rejects_invalid_values(values):
    """Nieznane klucze i logiczne liczby nie zmieniają zasad administratora."""
    with pytest.raises(ValueError):
        PolicyValues(**values)


def shipment(identifier, source="shipping_orbit", *, order=8, item=1, direction="outbound"):
    """Wysyłka ma numer zlecenia, rok, kierunek i numer listu."""
    return {
        "id": identifier,
        "record_id": identifier,
        "kind": "shipment",
        "device_id": "ms:7",
        "source": source,
        "status": "confirmed",
        "data": {
            "order_id": order,
            "order_year": 2026,
            "direction": direction,
            "tracking_number": "12345",
            "item_row_id": item,
            "item_id": item,
            "color": "black",
            "quantity": "1",
        },
    }


def test_shipping_ms_one_event_two_evidence_no_extra_quantity():
    """Nagłówek MS nie dodaje drugiej sztuki do tej samej wysyłki."""
    events, evidence = consolidate([shipment("shipping"), shipment("ms", "ms_orbit")])
    assert len(events) == 1
    assert len(evidence) == 2
    assert events[0]["data"]["items"][0]["quantity"] == "1"
    assert "quantity" not in events[0]["data"]


def test_consolidation_preserves_orders_items_and_direction():
    """Wspólny list nie łączy ilości innych zleceń ani przesyłki przychodzącej."""
    events, _ = consolidate(
        [
            shipment("one"),
            shipment("two", item=2),
            shipment("three", order=9),
            shipment("four", "ms_orbit", direction="inbound"),
        ]
    )
    assert len(events) == 3
    assert sorted(len(event["data"]["items"]) for event in events) == [0, 1, 2]


def test_missing_identity_and_conflict_are_not_merged():
    """Niejednoznaczność nigdy nie jest rozstrzygana samym podobieństwem dat."""
    event = shipment("bad", order=None)
    events, _ = consolidate([event, shipment("good")])
    assert len(events) == 2
    first, second = shipment("one"), shipment("two")
    second["data"]["quantity"] = "2"
    events, _ = consolidate([first, second])
    assert events[0]["status"] == "conflict"


def test_reader_grants_only_select_and_rejects_unsafe_users():
    """Instalator nie rozszerza operacji na zapisy ani konto administratora."""
    assert grant_statements("CTIP_READER", ["KLIENT"]) == [
        'GRANT SELECT ON "KLIENT" TO USER "CTIP_READER"'
    ]
    for user in ("SYSDBA", "reader;DROP", "", "reader"):
        with pytest.raises(ValueError):
            grant_statements(user, ["KLIENT"])
