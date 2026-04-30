"""Testy logiki raportu MM."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from app.services.mm_dashboard import (
    DEST_WYNAJEM,
    DEST_ZLOM,
    extract_model_label,
    load_mm_dashboard_data,
)


class _FakeCursor:
    def __init__(self) -> None:
        self.closed = False
        self.executed: list[tuple[str, tuple[object, ...] | None]] = []

    def execute(self, query: str, params: tuple[object, ...] | None = None) -> None:
        self.executed.append((query, params))

    def fetchall(self) -> list[tuple[object, ...]]:
        last_query = self.executed[-1][0]
        if "FROM MAGAZYNY" in last_query:
            return [
                (3, "Magazyn Złom"),
                (27, "Urzadzenia Wynajem"),
                (28, "Urządzenia Magazyn"),
                (5, "Magazyn Glowne"),
            ]
        return [
            (
                1001,
                "MM/1/2026",
                date(2026, 1, 5),
                27,
                28,
                "uwaga a",
                5001,
                "IDX-RICOH",
                "Ricoh IM C300 S/N: SN123 nr.wew 882",
                Decimal("1.00"),
                "szt",
                "SN123",
                "KP/123",
                Decimal("450.50"),
                Decimal("460.00"),
            ),
            (
                1001,
                "MM/1/2026",
                date(2026, 1, 5),
                27,
                28,
                "uwaga a",
                5002,
                "IDX-RICOH-2",
                "Ricoh IM C300",
                Decimal("1.00"),
                "szt",
                "SN124",
                "KP/124",
                Decimal("470.00"),
                Decimal("480.00"),
            ),
            (
                1002,
                "MM/2/2026",
                date(2026, 1, 6),
                3,
                27,
                "uwaga b",
                5003,
                "IDX-KONICA",
                "Konica Minolta C250 S/N: XY",
                Decimal("2.00"),
                "szt",
                "XY",
                "KP/200",
                Decimal("300.00"),
                Decimal("305.00"),
            ),
            (
                1003,
                "MM/3/2026",
                date(2026, 1, 7),
                27,
                5,
                "nie urzadzenie",
                5004,
                "IDX-TONER",
                "Toner czarny",
                Decimal("1.00"),
                "szt",
                "",
                "",
                Decimal("50.00"),
                Decimal("55.00"),
            ),
        ]

    def close(self) -> None:
        self.closed = True


class _FakeConnection:
    def __init__(self) -> None:
        self.cursor_obj = _FakeCursor()
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self.cursor_obj

    def close(self) -> None:
        self.closed = True


def test_extract_model_label_strips_sn_and_internal_number() -> None:
    assert extract_model_label("Ricoh IM C300 S/N: SN123 nr.wew 882") == "Ricoh IM C300"
    assert extract_model_label("", "IDX-1") == "IDX-1"


def test_load_mm_dashboard_data_returns_summary_for_all_targets() -> None:
    fake_connection = _FakeConnection()
    with patch("app.services.mm_dashboard._firebird_connection", return_value=fake_connection):
        result = load_mm_dashboard_data(
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            destination_filter="all",
        )

    assert result["summary"]["documents_count"] == 2
    assert result["summary"]["items_count"] == 3
    assert result["summary"]["quantity_sum"] == 4.0
    assert result["summary"]["documents_by_destination"]["Magazyn Złom (zlom)"] == 1
    assert result["summary"]["documents_by_destination"]["Urzadzenia Wynajem (wynajem)"] == 1
    assert result["warehouses"]["zlom_ids"] == [3]
    assert result["warehouses"]["wynajem_ids"] == [27]
    assert result["warehouses"]["source_ids"] == [27, 28]
    assert result["items"][0]["model_label"] == "Ricoh IM C300"
    assert result["items"][0]["cena_zakupu_netto"] == 450.5
    assert fake_connection.cursor_obj.closed is True
    assert fake_connection.closed is True


def test_load_mm_dashboard_data_filters_by_destination_model_and_search() -> None:
    fake_connection = _FakeConnection()
    with patch("app.services.mm_dashboard._firebird_connection", return_value=fake_connection):
        result_wynajem = load_mm_dashboard_data(
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            destination_filter=DEST_WYNAJEM,
            model_filter="ricoh",
            search_filter="SN124",
        )
        result_zlom = load_mm_dashboard_data(
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            destination_filter=DEST_ZLOM,
            model_filter="konica",
        )

    assert result_wynajem["summary"]["documents_count"] == 1
    assert result_wynajem["summary"]["items_count"] == 1
    assert result_wynajem["items"][0]["serial"] == "SN124"
    assert result_zlom["summary"]["documents_count"] == 1
    assert result_zlom["items"][0]["destination_kind"] == DEST_ZLOM
