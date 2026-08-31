"""Testy skanera sekretów w śledzonych plikach."""

from scripts.secret_scan import scan_text


def test_scanner_reports_location_without_secret_value() -> None:
    """Wynik zawiera klucz i lokalizację, ale nie przechowuje sekretu."""
    secret = "bardzo-poufna-wartosc"
    issues = scan_text(
        path="config.txt",
        text="{}={}\n".format("SMS_API_TOKEN", secret),
        exact_secrets={"SMS_API_TOKEN": secret},
    )

    assert issues
    rendered = " ".join(f"{item.path}:{item.line}:{item.kind}" for item in issues)
    assert secret not in rendered
    assert "SMS_API_TOKEN" in rendered


def test_scanner_accepts_empty_and_variable_placeholders() -> None:
    """Przykładowe konfiguracje bez wartości nie generują alarmu."""
    issues = scan_text(
        path=".env.example",
        text="EMAIL_PASSWORD=\nADMIN_SECRET_KEY=${ADMIN_SECRET_KEY}\n",
        exact_secrets={},
    )
    assert issues == []
