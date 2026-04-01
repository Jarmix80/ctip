import os

import gspread

SPREADSHEET_ID = "1CRUJ3Mlw3HpiXMct6c-d6P3l7gStVboQW27irqyA7Bs"


def main():
    cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    print("GOOGLE_APPLICATION_CREDENTIALS =", cred_path)
    if not cred_path:
        raise SystemExit("ERROR: missing GOOGLE_APPLICATION_CREDENTIALS env var")

    gc = gspread.service_account(filename=cred_path)
    sh = gc.open_by_key(SPREADSHEET_ID)

    ws = sh.worksheet("Urzadzenia")
    headers = ws.row_values(1)
    print("HEADERS:", headers)

    ws.append_row(
        [
            "TEST_PROD",
            "TEST_MODEL",
            "SN-TEST-001",
            "WEW-TEST-001",
            "Przed zerowka",
            0,
            0,
            0,
            "Magazyn KP",
            "Brak rezerwacji",
            "Wiersz testowy",
        ],
        value_input_option="USER_ENTERED",
    )

    print("OK: appended test row to sheet 'Urzadzenia'")


if __name__ == "__main__":
    main()
