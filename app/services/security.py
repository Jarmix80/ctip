"""Funkcje bezpieczeństwa dla panelu administracyjnego."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from typing import Final

PBKDF2_ROUNDS: Final[int] = 480_000
SALT_LEN: Final[int] = 16
PASSWORD_MIN_LENGTH: Final[int] = 9
PASSWORD_POLICY_MESSAGE: Final[str] = (
    "Hasło musi mieć co najmniej 9 znaków oraz zawierać co najmniej jedną wielką literę, "
    "jedną cyfrę i jeden znak specjalny."
)


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


def hash_session_token(token: str) -> str:
    """Zwraca skrót SHA-256 tokenu sesji przechowywany w bazie."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_password_policy(password: str) -> None:
    """Waliduje politykę złożoności hasła."""
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(PASSWORD_POLICY_MESSAGE)
    if re.search(r"[A-Z]", password) is None:
        raise ValueError(PASSWORD_POLICY_MESSAGE)
    if re.search(r"\d", password) is None:
        raise ValueError(PASSWORD_POLICY_MESSAGE)
    if re.search(r"[^A-Za-z0-9]", password) is None:
        raise ValueError(PASSWORD_POLICY_MESSAGE)
