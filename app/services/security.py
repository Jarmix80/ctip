"""Funkcje bezpieczeństwa dla panelu administracyjnego."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Final

PBKDF2_ROUNDS: Final[int] = 480_000
SALT_LEN: Final[int] = 16


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data.encode("ascii"))


def hash_password(password: str) -> str:
    """Zwraca skrót hasła w formacie pbkdf2_sha256$rounds$salt$hash."""
    if not password:
        raise ValueError("Hasło nie może być puste")
    salt = secrets.token_bytes(SALT_LEN)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${_b64encode(salt)}${_b64encode(dk)}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Porównuje hasło z zapisanym skrótem."""
    try:
        algo, rounds_str, salt_b64, hash_b64 = stored_hash.split("$", maxsplit=3)
        if algo != "pbkdf2_sha256":
            return False
        rounds = int(rounds_str)
        salt = _b64decode(salt_b64)
        expected = _b64decode(hash_b64)
    except (ValueError, TypeError):
        return False

    computed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(expected, computed)


def generate_session_token() -> str:
    """Generuje losowy token sesji."""
    return secrets.token_urlsafe(32)
