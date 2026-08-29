from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

from app.core import asyncio_compat


def load_module():
    """Ładuje skrypt backfillu jako moduł testowy bez wykonywania operacji."""
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "dpd_infoservices_backfill.py"
    spec = importlib.util.spec_from_file_location(
        "dpd_infoservices_backfill_test_module", module_path
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_konfiguruje_petle_przed_uruchomieniem_backfillu() -> None:
    """Potwierdza ustawienie zgodnej pętli przed utworzeniem korutyny głównej."""
    backfill = load_module()

    def close_coroutine(coroutine):
        coroutine.close()
        return 0

    with (
        patch.object(asyncio_compat, "configure_asyncio_for_windows") as configure_mock,
        patch.object(backfill.asyncio, "run", side_effect=close_coroutine) as run_mock,
    ):
        result = backfill.run()

    assert result == 0
    configure_mock.assert_called_once_with()
    run_mock.assert_called_once()
