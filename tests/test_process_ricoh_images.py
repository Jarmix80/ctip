from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from PIL import Image

SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / "inbox" / "audyt_model" / "process_ricoh_images.py"
)
SPEC = importlib.util.spec_from_file_location("process_ricoh_images", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_slugify_model_normalizuje_model_ricoh() -> None:
    assert MODULE.slugify_model("MP C2004ex SP") == "ricoh_mp_c2004ex_sp"
    assert MODULE.slugify_model("IM C530FB") == "ricoh_im_c530fb"
    assert MODULE.slugify_model("MP6055") == "ricoh_mp6055"


def test_iter_models_obsluguje_pusty_pierwszy_wiersz_i_naglowek_model(tmp_path: Path) -> None:
    csv_path = tmp_path / "modele.csv"
    csv_path.write_text(
        "\nMODEL;IMAGE_OUT\nMP C3003;/imgdev/ricoh_mp_c3003.png\nIM 3500;\n",
        encoding="utf-8",
    )

    rows = list(MODULE.iter_models(csv_path))

    assert [row.model for row in rows] == ["MP C3003", "IM 3500"]
    assert rows[0].image_out == "/imgdev/ricoh_mp_c3003.png"


def test_build_output_name_szanuje_image_out() -> None:
    assert (
        MODULE.build_output_name("ricoh_mp_c3003", "/imgdev/ricoh_mp_c3003.png")
        == "ran_ricoh_mp_c3003.png"
    )
    assert MODULE.build_output_name("ricoh_mp_c3003", "packshot.jpg") == "ran_packshot.png"
    assert MODULE.build_output_name("ricoh_mp_c3003", "") == "ran_ricoh_mp_c3003.png"


def test_resolve_image_in_rozpoznaje_basename_z_url(tmp_path: Path) -> None:
    src_dir = tmp_path / "imgsrc"
    src_dir.mkdir()
    image_path = src_dir / "ricoh_mp_c3003.png"
    image_path.write_bytes(b"png")

    resolved = MODULE.resolve_image_in(
        "https://ksero-partner.com.pl/imgdev/ricoh_mp_c3003.png",
        src_dir,
    )

    assert resolved == image_path


def test_has_transparency_wykrywa_rzeczywisty_kanal_alfa(tmp_path: Path) -> None:
    transparent_path = tmp_path / "transparent.png"
    opaque_path = tmp_path / "opaque.png"

    transparent = Image.new("RGBA", (10, 10), (255, 255, 255, 0))
    transparent.save(transparent_path)

    opaque = Image.new("RGBA", (10, 10), (255, 255, 255, 255))
    opaque.save(opaque_path)

    assert MODULE.has_transparency(transparent_path) is True
    assert MODULE.has_transparency(opaque_path) is False


def test_has_transparency_wykrywa_przezroczystosc_palety_png(tmp_path: Path) -> None:
    palette_path = tmp_path / "palette.png"

    image = Image.new("P", (4, 4))
    image.putpalette(
        [
            0,
            0,
            0,
            255,
            255,
            255,
        ]
        + [0, 0, 0] * 254
    )
    image.paste(1, (0, 0, 4, 4))
    image.putpixel((0, 0), 0)
    image.info["transparency"] = 0
    image.save(palette_path)

    assert MODULE.has_transparency(palette_path) is True


def test_prepare_rgba_image_czysci_packshot_png_z_bialym_wnetrzem(tmp_path: Path) -> None:
    source_path = tmp_path / "packshot.png"
    image = Image.new("RGBA", (120, 160), (0, 0, 0, 0))
    for x in range(5, 115):
        for y in range(5, 155):
            image.putpixel((x, y), (255, 255, 255, 255))
    for x in range(32, 88):
        for y in range(30, 140):
            image.putpixel((x, y), (220, 220, 220, 255))
    image.save(source_path)

    result, method = MODULE.prepare_rgba_image(source_path, tmp_path / "tmp.png", "rembg")

    assert method == "alpha+cleanup"
    bbox = MODULE.alpha_bbox(result)
    assert bbox is not None
    assert bbox[0] <= 32
    assert bbox[1] <= 30
    assert bbox[2] >= 88
    assert bbox[3] >= 140


def test_normalize_path_respektuje_sciezke_wzgledem_biezacego_katalogu(
    tmp_path: Path, monkeypatch
) -> None:
    current_dir = tmp_path / "workspace"
    relative_dir = current_dir / "imgsrc"
    relative_dir.mkdir(parents=True)
    monkeypatch.chdir(current_dir)

    normalized = MODULE.normalize_path(Path("imgsrc"))

    assert normalized == relative_dir.resolve()


def test_add_shadow_and_fit_zwraca_canvas_docelowy() -> None:
    image = Image.new("RGBA", (500, 500), (0, 0, 0, 0))
    for x in range(80, 420):
        for y in range(60, 440):
            image.putpixel((x, y), (240, 240, 240, 255))

    result = MODULE.add_shadow_and_fit(image)

    assert result.size == MODULE.CANVAS
    alpha = result.getchannel("A")
    assert alpha.getbbox() is not None


def test_apply_logo_and_white_background_naklada_logo_i_zmienia_tryb(tmp_path: Path) -> None:
    image = Image.new("RGBA", MODULE.CANVAS, (0, 0, 0, 0))
    for x in range(350, 850):
        for y in range(300, 1400):
            image.putpixel((x, y), (220, 220, 220, 255))

    logo_path = tmp_path / "logo.png"
    logo = Image.new("RGBA", (100, 50), (200, 0, 0, 255))
    logo.save(logo_path)

    result = MODULE.apply_logo_and_white_background(image, logo_path)

    assert result.size == MODULE.CANVAS
    assert result.mode == "RGB"
    assert result.getpixel((40, 40)) == (200, 0, 0)
    assert result.getpixel((0, 0)) == (255, 255, 255)
