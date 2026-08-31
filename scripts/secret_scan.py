"""Skanuje śledzone pliki pod kątem sekretów bez wypisywania ich wartości."""

from __future__ import annotations

import argparse
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SENSITIVE_KEYS = (
    "ADMIN_SECRET_KEY",
    "BOT_IDENTITY_SECRET_KEY",
    "CRM_LAB_IFRAME_SECRET",
    "DPD_PASSWORD",
    "EMAIL_PASSWORD",
    "FB_PASSWORD",
    "FB_V_PASSWORD",
    "GOOGLE_PRIVATE_KEY",
    "OFFICE365_CLIENT_SECRET",
    "OPENAI_API_KEY",
    "PGPASSWORD",
    "SMS_API_PASSWORD",
    "SMS_API_TOKEN",
)
ASSIGNMENT_RE = re.compile(
    rf"[\"']?(?P<key>{'|'.join(map(re.escape, SENSITIVE_KEYS))})[\"']?\s*[:=]\s*(?P<value>[^\s,;]+)"
)
TOKEN_PATTERNS = (
    ("klucz prywatny", re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----")),
    ("token GitHub", re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}\b")),
    ("klucz OpenAI", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
)
PLACEHOLDER_VALUES = {
    "",
    "''",
    '""',
    "false",
    "none",
    "null",
    "masterkey",
    "ctip_test",
    "ctip-test-only",
    "test",
    "changeme",
    "change_me",
    "zmien_mnie",
    "dummy",
    "example",
    "not-set",
    "not_set",
    "xxxxxxxx",
}
EXACT_VALUE_ALLOWED = {
    "FB_PASSWORD": (
        ".env.test.example",
        "README.md",
        "app/core/config.py",
        "compose.test.yml",
        "deploy/bot-identity.runtime.example",
        "tests/",
    ),
    "PGPASSWORD": (".env.test.example", "docs/instal/test_prod_mirror.md", "tests/"),
}


@dataclass(frozen=True)
class SecretIssue:
    """Lokalizacja podejrzanej wartości bez jej treści."""

    path: str
    line: int
    kind: str


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().strip("'\"").rstrip(",").lower()
    return (
        normalized in PLACEHOLDER_VALUES
        or normalized.startswith("${")
        or normalized.startswith("<")
        or "example.invalid" in normalized
        or set(normalized) <= {"x", "-", "_"}
    )


def _read_exact_env_values(path: Path) -> dict[str, str]:
    """Czyta wyłącznie kontrolowaną listę kluczy z lokalnego `.env`."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    patterns = {
        key: re.compile(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=(.*)$") for key in SENSITIVE_KEYS
    }
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        for key, pattern in patterns.items():
            match = pattern.match(line)
            if not match:
                continue
            value = match.group(1).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            if len(value) >= 8:
                values[key] = value
    return values


def _exact_value_allowed(path: str, key: str) -> bool:
    return any(
        path == prefix or path.startswith(prefix) for prefix in EXACT_VALUE_ALLOWED.get(key, ())
    )


def scan_text(
    *,
    path: str,
    text: str,
    exact_secrets: dict[str, str],
) -> list[SecretIssue]:
    """Zwraca lokalizacje podejrzeń bez zachowywania wartości w wyniku."""
    issues: list[SecretIssue] = []
    assignment_scan_enabled = Path(path).name.startswith(".env") or Path(path).suffix.lower() in {
        "",
        ".env",
        ".json",
        ".md",
        ".sh",
        ".txt",
        ".yaml",
        ".yml",
    }
    for number, line in enumerate(text.splitlines(), 1):
        for label, pattern in TOKEN_PATTERNS:
            if pattern.search(line):
                issues.append(SecretIssue(path, number, label))
        if assignment_scan_enabled:
            for match in ASSIGNMENT_RE.finditer(line):
                if line[max(0, match.start() - 2) : match.start()] == "${":
                    continue
                if not _is_placeholder(match.group("value")):
                    issues.append(
                        SecretIssue(path, number, f"wartość {match.group('key').upper()}")
                    )
        for key, secret in exact_secrets.items():
            if secret in line and not _exact_value_allowed(path, key):
                issues.append(SecretIssue(path, number, f"wartość zgodna z lokalnym {key}"))
    return list(dict.fromkeys(issues))


def tracked_files() -> Iterable[Path]:
    """Zwraca pliki śledzone przez Git, bez katalogów roboczych i sekretów lokalnych."""
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
    )
    for value in completed.stdout.decode().split("\0"):
        if value:
            yield PROJECT_ROOT / value


def main() -> int:
    """Skanuje bieżące drzewo i zwraca kod 1 po wykryciu sekretu."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    args = parser.parse_args()
    exact_secrets = _read_exact_env_values(args.env_file)
    issues: list[SecretIssue] = []
    for file_path in tracked_files():
        try:
            text = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        relative = file_path.relative_to(PROJECT_ROOT).as_posix()
        issues.extend(scan_text(path=relative, text=text, exact_secrets=exact_secrets))
    if issues:
        for issue in sorted(set(issues), key=lambda item: (item.path, item.line, item.kind)):
            print(f"[BŁĄD] {issue.path}:{issue.line}: {issue.kind}; wartość ukryta.")
        return 1
    print("[OK] Nie wykryto sekretów w śledzonych plikach.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
