from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch


def load_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "windows" / "run_ctip_web.py"
    spec = importlib.util.spec_from_file_location("run_ctip_web_test_module", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_main_uruchamia_uvicorn_z_produkcyjnymi_parametrami() -> None:
    run_ctip_web = load_module()

    with (
        patch.object(run_ctip_web, "configure_event_loop_policy") as configure_mock,
        patch.object(run_ctip_web.uvicorn, "run") as uvicorn_run,
    ):
        run_ctip_web.main()

    configure_mock.assert_called_once_with()
    uvicorn_run.assert_called_once_with("app.main:app", host="0.0.0.0", port=8000, workers=1)
