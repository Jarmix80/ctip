"""Aktualizacja widoków kalendarza i formatowania arkusza Urzadzenia."""

from __future__ import annotations

import calendar
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

import gspread
from google.oauth2.service_account import Credentials

SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
)

SHEET_CALENDAR = "Kalendarz_wiersze"
SHEET_DEVICES = "Urzadzenia"
SHEET_EVENTS_SOURCES = ("Arkusz2", "Kalendarz")

EVENT_TYPES = ("DOWOZ", "ODBIOR", "WYSYLKA")
WEEKDAY_PL = (
    "Poniedzialek",
    "Wtorek",
    "Sroda",
    "Czwartek",
    "Piatek",
    "Sobota",
    "Niedziela",
)


@dataclass(slots=True)
class CalendarEvent:
    event_date: date
    event_type: str
    description: str


def normalize(value: str | None) -> str:
    return (value or "").strip().lower()


def parse_date(value: str | None) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    text = text.replace("/", "-").replace(".", "-").split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d-%m-%y"):
        try:
            parsed = datetime.strptime(text, fmt).date()
            if parsed.year < 100:
                return parsed.replace(year=2000 + parsed.year)
            return parsed
        except ValueError:
            continue
    return None


def detect_columns(headers: list[str]) -> tuple[int | None, int | None, int | None]:
    normalized = [normalize(item) for item in headers]
    date_idx = None
    type_idx = None
    desc_idx = None
    for idx, item in enumerate(normalized):
        if date_idx is None and any(token in item for token in ("data", "termin", "date")):
            date_idx = idx
        if type_idx is None and any(
            token in item for token in ("typ", "rodzaj", "kategoria", "zdarzen", "wydarzen")
        ):
            type_idx = idx
        if desc_idx is None and any(
            token in item for token in ("opis", "uwag", "klient", "adres", "nazwa", "szczegol")
        ):
            desc_idx = idx
    return date_idx, type_idx, desc_idx


def classify_event_type(raw_value: str | None) -> str:
    value = normalize(raw_value)
    if "dow" in value or "dostaw" in value:
        return "DOWOZ"
    if "odb" in value or "zwrot" in value:
        return "ODBIOR"
    if "wys" in value or "kurier" in value:
        return "WYSYLKA"
    return "INNE"


def clean_description(value: str | None) -> str:
    text = (value or "").strip()
    text = re.sub(r"^[\-\u2022]\s*", "", text)
    text = re.sub(r"\s+\|\s+", " | ", text)
    if not text:
        return "(brak opisu)"
    return text


def read_source_events(workbook: gspread.Spreadsheet) -> list[CalendarEvent]:
    events: list[CalendarEvent] = []
    available = {ws.title for ws in workbook.worksheets()}
    for sheet_name in SHEET_EVENTS_SOURCES:
        if sheet_name not in available:
            continue
        worksheet = workbook.worksheet(sheet_name)
        values = worksheet.get_all_values()
        if len(values) < 2:
            continue
        date_idx, type_idx, desc_idx = detect_columns(values[0])
        for row in values[1:]:
            if not any((column or "").strip() for column in row):
                continue
            event_date = parse_date(
                row[date_idx] if date_idx is not None and date_idx < len(row) else ""
            )
            if event_date is None:
                continue
            event_type = classify_event_type(
                row[type_idx] if type_idx is not None and type_idx < len(row) else ""
            )
            if event_type not in EVENT_TYPES:
                continue
            description = ""
            if desc_idx is not None and desc_idx < len(row):
                description = clean_description(row[desc_idx])
            if not description or description == "(brak opisu)":
                extras = [
                    (column or "").strip()
                    for idx, column in enumerate(row)
                    if idx not in {date_idx, type_idx, desc_idx} and (column or "").strip()
                ]
                description = clean_description(" | ".join(extras[:3]))
            events.append(
                CalendarEvent(
                    event_date=event_date,
                    event_type=event_type,
                    description=description,
                )
            )

    unique: list[CalendarEvent] = []
    seen: set[tuple[str, str, str]] = set()
    for item in events:
        key = (item.event_date.isoformat(), item.event_type, item.description)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def build_calendar_rows(events: list[CalendarEvent]) -> tuple[str, list[list[str]], int]:
    month_counter: defaultdict[tuple[int, int], int] = defaultdict(int)
    for item in events:
        month_counter[(item.event_date.year, item.event_date.month)] += 1
    if not month_counter:
        raise RuntimeError("Brak zdarzen do aktualizacji kalendarza.")
    (year, month), _ = sorted(month_counter.items(), key=lambda part: part[1], reverse=True)[0]

    events_by_day: defaultdict[int, list[CalendarEvent]] = defaultdict(list)
    for item in events:
        if item.event_date.year == year and item.event_date.month == month:
            events_by_day[item.event_date.day].append(item)

    for day in events_by_day:
        events_by_day[day].sort(key=lambda event: (event.event_type, event.description))

    rows: list[list[str]] = [
        [f"KALENDARZ WIERSZOWY - {year:04d}-{month:02d}"],
        [
            "Data",
            "Dzien tygodnia",
            "Typ",
            "Opis",
            "Priorytet",
            "Status",
            "Uwagi",
        ],
        [
            "Instrukcja",
            "",
            "Wybierz typ",
            "Uzupelnij opis",
            "",
            "Plan",
            "Wpisy dodawaj w pustych slotach pod dniem",
        ],
    ]

    _, days_in_month = calendar.monthrange(year, month)
    total_records = 0
    slot_count = 8
    for day in range(1, days_in_month + 1):
        day_date = date(year, month, day)
        rows.append(
            [
                day_date.strftime("%d-%m-%Y"),
                WEEKDAY_PL[day_date.weekday()],
                "",
                "",
                "",
                "",
                "",
            ]
        )
        day_events = events_by_day.get(day, [])
        total_records += len(day_events)
        for idx in range(slot_count):
            if idx < len(day_events):
                event = day_events[idx]
                rows.append(
                    [
                        "",
                        "",
                        event.event_type,
                        event.description,
                        "",
                        "Plan",
                        "",
                    ]
                )
            else:
                rows.append(["", "", "", "", "", "", ""])
        rows.append(["", "", "", "", "", "", ""])

    return f"{year:04d}-{month:02d}", rows, total_records


def apply_calendar_style(
    workbook: gspread.Spreadsheet, worksheet: gspread.Worksheet, rows_count: int
) -> None:
    requests: list[dict] = [
        {
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 7,
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True, "fontSize": 12},
                        "backgroundColor": {"red": 0.86, "green": 0.95, "blue": 0.89},
                    }
                },
                "fields": "userEnteredFormat(textFormat,backgroundColor)",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": 1,
                    "endRowIndex": 2,
                    "startColumnIndex": 0,
                    "endColumnIndex": 7,
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True},
                        "horizontalAlignment": "CENTER",
                        "backgroundColor": {"red": 0.78, "green": 0.88, "blue": 0.97},
                    }
                },
                "fields": "userEnteredFormat(textFormat,horizontalAlignment,backgroundColor)",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": 2,
                    "endRowIndex": 3,
                    "startColumnIndex": 0,
                    "endColumnIndex": 7,
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"italic": True, "fontSize": 10},
                        "backgroundColor": {"red": 0.98, "green": 0.99, "blue": 1.0},
                    }
                },
                "fields": "userEnteredFormat(textFormat,backgroundColor)",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": 3,
                    "startColumnIndex": 0,
                    "endColumnIndex": 7,
                },
                "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"}},
                "fields": "userEnteredFormat(wrapStrategy,verticalAlignment)",
            }
        },
        {
            "updateSheetProperties": {
                "properties": {"sheetId": worksheet.id, "gridProperties": {"frozenRowCount": 2}},
                "fields": "gridProperties.frozenRowCount",
            }
        },
        {
            "setBasicFilter": {
                "filter": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": 7,
                    }
                }
            }
        },
    ]

    widths = [110, 130, 110, 420, 90, 100, 260]
    for idx, width in enumerate(widths):
        requests.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": worksheet.id,
                        "dimension": "COLUMNS",
                        "startIndex": idx,
                        "endIndex": idx + 1,
                    },
                    "properties": {"pixelSize": width},
                    "fields": "pixelSize",
                }
            }
        )

    for row_idx in range(3, rows_count):
        requests.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": worksheet.id,
                        "dimension": "ROWS",
                        "startIndex": row_idx,
                        "endIndex": row_idx + 1,
                    },
                    "properties": {"pixelSize": 28},
                    "fields": "pixelSize",
                }
            }
        )

    workbook.batch_update({"requests": requests})

    validation_request = {
        "requests": [
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": 3,
                        "endRowIndex": rows_count,
                        "startColumnIndex": 2,
                        "endColumnIndex": 3,
                    },
                    "rule": {
                        "condition": {
                            "type": "ONE_OF_LIST",
                            "values": [{"userEnteredValue": value} for value in EVENT_TYPES],
                        },
                        "showCustomUi": True,
                        "strict": False,
                    },
                }
            }
        ]
    }
    workbook.batch_update(validation_request)


def apply_devices_style(workbook: gspread.Spreadsheet, worksheet: gspread.Worksheet) -> None:
    values = worksheet.get_all_values()
    rows_count = max(len(values), 2)
    cols_count = max(len(values[0]) if values else 0, 8)

    requests: list[dict] = [
        {
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": cols_count,
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True},
                        "horizontalAlignment": "CENTER",
                        "backgroundColor": {"red": 0.82, "green": 0.90, "blue": 0.97},
                    }
                },
                "fields": "userEnteredFormat(textFormat,horizontalAlignment,backgroundColor)",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": 1,
                    "endRowIndex": rows_count,
                    "startColumnIndex": 0,
                    "endColumnIndex": cols_count,
                },
                "cell": {
                    "userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "MIDDLE"}
                },
                "fields": "userEnteredFormat(wrapStrategy,verticalAlignment)",
            }
        },
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": worksheet.id,
                    "gridProperties": {"frozenRowCount": 1},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        },
        {
            "setBasicFilter": {
                "filter": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": 0,
                        "endRowIndex": rows_count,
                        "startColumnIndex": 0,
                        "endColumnIndex": cols_count,
                    }
                }
            }
        },
    ]

    default_width = 140
    for idx in range(cols_count):
        width = 170 if idx in {2, 3, 8, 10, 11} else default_width
        requests.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": worksheet.id,
                        "dimension": "COLUMNS",
                        "startIndex": idx,
                        "endIndex": idx + 1,
                    },
                    "properties": {"pixelSize": width},
                    "fields": "pixelSize",
                }
            }
        )
    workbook.batch_update({"requests": requests})


def open_workbook() -> gspread.Spreadsheet:
    credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    spreadsheet_id = os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID")
    if not credentials_path:
        raise RuntimeError("Brak GOOGLE_APPLICATION_CREDENTIALS.")
    if not spreadsheet_id:
        raise RuntimeError("Brak GOOGLE_SHEETS_SPREADSHEET_ID.")
    credentials = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
    client = gspread.authorize(credentials)
    return client.open_by_key(spreadsheet_id)


def main() -> None:
    workbook = open_workbook()
    sheet_titles = {worksheet.title for worksheet in workbook.worksheets()}
    if SHEET_CALENDAR in sheet_titles:
        calendar_sheet = workbook.worksheet(SHEET_CALENDAR)
        calendar_sheet.clear()
    else:
        calendar_sheet = workbook.add_worksheet(title=SHEET_CALENDAR, rows=1500, cols=10)

    source_events = read_source_events(workbook)
    selected_month, calendar_rows, imported_count = build_calendar_rows(source_events)
    calendar_sheet.update(values=calendar_rows, range_name="A1", value_input_option="USER_ENTERED")
    apply_calendar_style(workbook, calendar_sheet, len(calendar_rows))

    if SHEET_DEVICES in sheet_titles:
        apply_devices_style(workbook, workbook.worksheet(SHEET_DEVICES))

    print("OK")
    print(f"MIESIAC={selected_month}")
    print(f"ZDARZENIA={imported_count}")
    print(f"ARKUSZ_KALENDARZ={SHEET_CALENDAR}")
    print(f"ARKUSZ_URZADZENIA={SHEET_DEVICES}")


if __name__ == "__main__":
    main()
