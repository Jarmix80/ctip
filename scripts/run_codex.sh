#!/usr/bin/env bash
set -euo pipefail

# Skrypt uruchamia Codex w kontekście repozytorium CTIP z aktywnym .venv i .env.

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

if [[ ! -f "AGENTS.md" ]]; then
  echo "Blad: brak AGENTS.md w katalogu repozytorium: $repo_dir" >&2
  exit 1
fi

if [[ ! -f ".env" ]]; then
  echo "Blad: brak pliku .env. Uzupelnij go na podstawie .env.example." >&2
  exit 1
fi

if [[ ! -d ".venv" ]]; then
  echo "Tworzenie .venv..."
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

set -a
# shellcheck disable=SC1091
source .env
set +a

export CODEX_HOME="${CODEX_HOME:-$repo_dir/.codex}"

if command -v codex >/dev/null 2>&1; then
  exec codex "$@"
fi

if command -v openai >/dev/null 2>&1; then
  exec openai codex "$@"
fi

if [[ -x ".venv/bin/codex" ]]; then
  exec .venv/bin/codex "$@"
fi

echo "Blad: nie znaleziono polecenia Codex w PATH ani w .venv." >&2
echo "Zainstaluj CLI Codex i dodaj je do PATH, a nastepnie uruchom skrypt ponownie." >&2
exit 1
