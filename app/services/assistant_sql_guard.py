"""Walidacja zapytań SQL dla narzędzia Firebird read-only."""

from __future__ import annotations

import re
from dataclasses import dataclass

_FORBIDDEN_KEYWORDS = (
    "insert",
    "update",
    "delete",
    "merge",
    "alter",
    "drop",
    "execute",
    "create",
    "grant",
    "revoke",
    "truncate",
    "comment",
    "commit",
    "rollback",
    "call",
    "set transaction",
    "set role",
)

_COMMENT_BLOCK_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMMENT_LINE_RE = re.compile(r"--[^\n\r]*")


class AssistantSqlGuardError(ValueError):
    """Błąd walidacji SQL dla asystenta."""


@dataclass(slots=True, frozen=True)
class GuardedSql:
    """Wynik walidacji i normalizacji zapytania SQL."""

    original_sql: str
    normalized_sql: str
    wrapped_sql: str
    row_limit: int


def _strip_sql_comments(sql: str) -> str:
    without_blocks = _COMMENT_BLOCK_RE.sub(" ", sql)
    without_lines = _COMMENT_LINE_RE.sub(" ", without_blocks)
    return without_lines


def _normalize_whitespace(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def _split_sql_statements(sql: str) -> list[str]:
    """Dzieli SQL na instrukcje z uwzględnieniem apostrofów."""
    statements: list[str] = []
    buf: list[str] = []
    in_single_quote = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'":
            if in_single_quote and i + 1 < len(sql) and sql[i + 1] == "'":
                buf.append(ch)
                buf.append(sql[i + 1])
                i += 2
                continue
            in_single_quote = not in_single_quote
            buf.append(ch)
            i += 1
            continue
        if ch == ";" and not in_single_quote:
            statement = "".join(buf).strip()
            if statement:
                statements.append(statement)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _check_forbidden_keywords(sql: str) -> None:
    lowered = f" {sql.lower()} "
    for keyword in _FORBIDDEN_KEYWORDS:
        pattern = rf"\b{re.escape(keyword)}\b"
        if re.search(pattern, lowered):
            raise AssistantSqlGuardError(f"Zapytanie zawiera niedozwoloną operację: `{keyword}`.")


def guard_readonly_sql(sql: str, *, row_limit: int) -> GuardedSql:
    """Waliduje SQL i zwraca wersję opakowaną limitem rekordów."""
    if row_limit < 1:
        raise AssistantSqlGuardError("Limit wierszy musi być dodatni.")
    if row_limit > 5000:
        raise AssistantSqlGuardError("Limit wierszy przekracza bezpieczny próg 5000.")
    raw_sql = str(sql or "").strip()
    if not raw_sql:
        raise AssistantSqlGuardError("Zapytanie SQL nie może być puste.")
    if len(raw_sql) > 20_000:
        raise AssistantSqlGuardError("Zapytanie SQL przekracza maksymalną długość 20 000 znaków.")

    sql_no_comments = _strip_sql_comments(raw_sql)
    normalized = _normalize_whitespace(sql_no_comments).rstrip(";")
    if not normalized:
        raise AssistantSqlGuardError("Zapytanie SQL po normalizacji jest puste.")

    statements = _split_sql_statements(normalized)
    if len(statements) != 1:
        raise AssistantSqlGuardError("Dozwolone jest wyłącznie pojedyncze zapytanie SQL.")
    statement = statements[0]
    lowered = statement.lower().lstrip()
    if not (lowered.startswith("select ") or lowered.startswith("with ")):
        raise AssistantSqlGuardError("Dozwolone są wyłącznie zapytania SELECT lub CTE (WITH).")

    _check_forbidden_keywords(statement)

    wrapped_sql = f"SELECT * FROM ({statement}) assistant_guarded_rows ROWS {row_limit}"
    return GuardedSql(
        original_sql=raw_sql,
        normalized_sql=statement,
        wrapped_sql=wrapped_sql,
        row_limit=row_limit,
    )


__all__ = ["AssistantSqlGuardError", "GuardedSql", "guard_readonly_sql"]
