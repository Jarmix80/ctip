"""Walidacja adresów i bezpieczne udostępnianie zdjęć modeli urządzeń."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit, urlunsplit

from app.core.config import settings

_IMAGE_REF_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif")


class ModelImageNotFoundError(FileNotFoundError):
    """Sygnalizuje brak bezpiecznie zarejestrowanego obrazu."""


class ModelImageTooLargeError(ValueError):
    """Sygnalizuje przekroczenie limitu rozmiaru obrazu."""


@dataclass(frozen=True)
class ModelImageContent:
    """Gotowa zawartość obrazu wraz z bezpiecznym typem MIME."""

    content: bytes
    content_type: str


def safe_device_image_url(value: object) -> str | None:
    """Akceptuje wyłącznie kontrolowany adres obrazu bez sekretów w URL-u."""
    raw = str(value or "").strip()
    if not raw or len(raw) > 2000 or any(character.isspace() for character in raw):
        return None
    parsed = urlsplit(raw)
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if (
        not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if scheme == "https":
        if hostname not in settings.bot_identity_image_https_hosts or port not in {None, 443}:
            return None
    elif scheme == "http":
        safe_lab = bool(
            settings.crm_lab_mode and settings.pg_database == "ctip_test" and settings.sms_test_mode
        )
        if not safe_lab or hostname not in settings.bot_identity_image_lab_http_hosts:
            return None
    else:
        return None
    decoded_path = unquote(parsed.path)
    if (
        not decoded_path
        or "://" in decoded_path
        or ".." in PurePosixPath(decoded_path).parts
        or not decoded_path.lower().endswith(_IMAGE_EXTENSIONS)
    ):
        return None
    return urlunsplit((scheme, parsed.netloc, parsed.path, "", ""))


def _content_type(content: bytes) -> str | None:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def load_model_image(image_ref: str) -> ModelImageContent:
    """Czyta wyłącznie obraz o nieprzewidywalnej nazwie z kontrolowanego katalogu."""
    if not _IMAGE_REF_PATTERN.fullmatch(str(image_ref or "")):
        raise ModelImageNotFoundError
    configured_root = str(settings.bot_identity_model_image_root or "").strip()
    if not configured_root:
        raise ModelImageNotFoundError
    root = Path(configured_root).expanduser()
    if not root.is_absolute():
        raise ModelImageNotFoundError
    root = root.resolve()
    max_bytes = max(1, settings.bot_identity_image_max_bytes)
    for extension in _IMAGE_EXTENSIONS:
        candidate = root / f"{image_ref}{extension}"
        if candidate.is_symlink():
            continue
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root) or not resolved.is_file():
            continue
        if resolved.stat().st_size > max_bytes:
            raise ModelImageTooLargeError
        content = resolved.read_bytes()
        if len(content) > max_bytes:
            raise ModelImageTooLargeError
        content_type = _content_type(content)
        if content_type is None:
            raise ModelImageNotFoundError
        return ModelImageContent(content=content, content_type=content_type)
    raise ModelImageNotFoundError
