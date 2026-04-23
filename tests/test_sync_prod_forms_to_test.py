"""Testy pomocników skryptu importu formularzy z produkcji do testu."""

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "sync_prod_forms_to_test.py"
SPEC = spec_from_file_location("sync_prod_forms_to_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

_coerce_fk = MODULE._coerce_fk
_normalize_statuses = MODULE._normalize_statuses


def test_normalize_statuses_uses_submitted_as_default() -> None:
    assert _normalize_statuses(None) == ["SUBMITTED"]
    assert _normalize_statuses([]) == ["SUBMITTED"]


def test_normalize_statuses_uppercases_and_deduplicates() -> None:
    assert _normalize_statuses(["submitted", "SUBMITTED", " approved "]) == [
        "SUBMITTED",
        "APPROVED",
    ]


def test_coerce_fk_returns_none_for_missing_or_unknown_ids() -> None:
    allowed = {1, 2, 3}
    assert _coerce_fk(None, allowed_ids=allowed) is None
    assert _coerce_fk("", allowed_ids=allowed) is None
    assert _coerce_fk("x", allowed_ids=allowed) is None
    assert _coerce_fk(99, allowed_ids=allowed) is None


def test_coerce_fk_preserves_existing_admin_user_id() -> None:
    assert _coerce_fk("2", allowed_ids={2, 3}) == 2
