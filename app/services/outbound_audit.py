"""Dzienny audyt komunikacji przechwyconej w środowisku testowym."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import getaddresses
from pathlib import Path
from threading import RLock
from typing import Any

from app.core.config import settings

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LOCK = RLock()


def _audit_directory() -> Path:
    configured = Path(settings.outbound_audit_dir).expanduser()
    path = configured if configured.is_absolute() else _PROJECT_ROOT / configured
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def _message_recipients(message: EmailMessage) -> list[str]:
    values: list[str] = []
    for header in ("To", "Cc", "Bcc"):
        values.extend(message.get_all(header, []))
    return [address for _, address in getaddresses(values) if address]


def _message_content(message: EmailMessage) -> str:
    if not message.is_multipart():
        content = message.get_content()
        return content.decode(errors="replace") if isinstance(content, bytes) else str(content)
    fallback = ""
    for part in message.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        content = part.get_content()
        text = content.decode(errors="replace") if isinstance(content, bytes) else str(content)
        if part.get_content_type() == "text/plain":
            return text
        if not fallback:
            fallback = text
    return fallback


def _prune_old_logs(directory: Path, now: datetime) -> None:
    retention_days = max(1, int(settings.outbound_audit_retention_days))
    threshold = now - timedelta(days=retention_days)
    for path in directory.glob("outbound_test_*.log"):
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=now.tzinfo)
            if modified < threshold:
                path.unlink()
        except OSError:
            continue


def record_outbound_attempt(
    *,
    channel: str,
    recipients: list[str],
    content: str,
    source: str,
    status: str,
    subject: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Path:
    """Zapisuje pełną treść i adresatów pojedynczej próby komunikacji."""
    now = datetime.now().astimezone()
    entry = {
        "timestamp": now.isoformat(),
        "channel": channel,
        "recipients": recipients,
        "subject": subject,
        "content": content,
        "source": source,
        "status": status,
        "metadata": metadata or {},
    }
    with _LOCK:
        directory = _audit_directory()
        _prune_old_logs(directory, now)
        path = directory / f"outbound_test_{now:%Y-%m-%d}.log"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        os.chmod(path, 0o600)
    return path


def record_email_attempt(
    message: EmailMessage,
    *,
    source: str,
    status: str,
    metadata: dict[str, Any] | None = None,
) -> Path:
    """Zapisuje raport próby wysyłki wiadomości e-mail."""
    return record_outbound_attempt(
        channel="email",
        recipients=_message_recipients(message),
        subject=str(message.get("Subject") or ""),
        content=_message_content(message),
        source=source,
        status=status,
        metadata=metadata,
    )


__all__ = ["record_email_attempt", "record_outbound_attempt"]
