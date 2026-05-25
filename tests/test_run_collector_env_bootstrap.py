from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "windows"
        / "run_collector_env_bootstrap.py"
    )
    spec = importlib.util.spec_from_file_location("run_collector_env_bootstrap", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_load_env_file_parses_values_and_quotes(tmp_path: Path) -> None:
    module = _load_module()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# komentarz",
                "PBX_HOST=192.168.0.11",
                "PBX_PORT='5524'",
                'PBX_PIN="1234"',
                "PUSTE=",
                "NIEPOPRAWNA",
            ]
        ),
        encoding="utf-8",
    )

    values = module.load_env_file(env_file)

    assert values["PBX_HOST"] == "192.168.0.11"
    assert values["PBX_PORT"] == "5524"
    assert values["PBX_PIN"] == "1234"
    assert values["PUSTE"] == ""
    assert "NIEPOPRAWNA" not in values


def test_load_env_file_returns_empty_dict_for_missing_file(tmp_path: Path) -> None:
    module = _load_module()
    values = module.load_env_file(tmp_path / "missing.env")
    assert values == {}


def test_ensure_repo_on_syspath_inserts_repo_only_once(tmp_path: Path) -> None:
    module = _load_module()
    original_syspath = list(module.sys.path)
    try:
        module.sys.path = ["alpha", "beta"]
        module.ensure_repo_on_syspath(tmp_path)
        module.ensure_repo_on_syspath(tmp_path)
        assert module.sys.path[0] == str(tmp_path)
        assert module.sys.path.count(str(tmp_path)) == 1
    finally:
        module.sys.path = original_syspath
