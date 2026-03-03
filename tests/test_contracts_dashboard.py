"""Testy pomocniczych funkcji dashboardu obslugi umow."""

from app.services.contracts_dashboard import normalize_device_key, normalize_nip


def test_normalize_nip_removes_non_digits() -> None:
    assert normalize_nip("777-000-11-22") == "7770001122"
    assert normalize_nip("  ") == ""


def test_normalize_device_key_keeps_alnum_uppercase() -> None:
    assert normalize_device_key(" sn-001 / ab ") == "SN001AB"
    assert normalize_device_key(None) == ""
