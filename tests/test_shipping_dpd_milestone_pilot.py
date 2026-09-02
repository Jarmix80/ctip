"""Testy parametrów kontrolowanego pilota archiwalnych statusów DPD."""

from __future__ import annotations

import pytest

from scripts.shipping_dpd_milestone_pilot import parse_order_number, parse_waybill


def test_numer_zlecenia_jest_normalizowany() -> None:
    """Parser zwraca identyfikator, rok i kanoniczną postać numeru."""
    assert parse_order_number(" 18517/2026 ") == (18517, 2026, "18517/2026")


@pytest.mark.parametrize("value", ["18517", "18517-2026", "abc/2026", "1/26"])
def test_niepoprawny_numer_zlecenia_jest_odrzucany(value: str) -> None:
    """Niepełny albo niejednoznaczny numer nie może wskazać celu pilota."""
    with pytest.raises(ValueError, match="format"):
        parse_order_number(value)


def test_numer_listu_dopuszcza_format_dpd() -> None:
    """Numer listu jest normalizowany bez zmiany liter i cyfr operatora."""
    assert parse_waybill(" 1050059395731U ") == "1050059395731U"


@pytest.mark.parametrize("value", ["", "123", "1050059395731U;rm", "numer listu"])
def test_niebezpieczny_numer_listu_jest_odrzucany(value: str) -> None:
    """Znaki powłoki i biały tekst nie mogą trafić do warstwy zapytań."""
    with pytest.raises(ValueError, match="Numer listu"):
        parse_waybill(value)
