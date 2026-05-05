from pathlib import Path

from app.web import form_ui


def test_public_form_templates_path_is_absolute() -> None:
    """Publiczne formularze musza ladowac szablony niezaleznie od katalogu roboczego."""
    loader = form_ui.templates.env.loader
    assert loader is not None
    searchpath = [Path(item) for item in getattr(loader, "searchpath", [])]
    assert searchpath
    assert searchpath[0].is_absolute()
    assert searchpath[0] == Path("app/templates").resolve()
