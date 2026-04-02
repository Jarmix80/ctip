#!/usr/bin/env bash
set -euo pipefail

# Skrypt uruchamia Codex w kontekście repozytorium CTIP z aktywnym .venv i lokalnym .env.test.

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

ENV_FILE="${ENV_FILE:-.env.test}"
PRECHECK_SCRIPT="scripts/codex_preflight.py"

if [[ ! -f "AGENTS.md" ]]; then
  echo "Blad: brak AGENTS.md w katalogu repozytorium: $repo_dir" >&2
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Blad: brak pliku ${ENV_FILE}. Uzupelnij go na podstawie .env.test.example." >&2
  exit 1
fi

if [[ ! -d ".venv" ]]; then
  echo "Tworzenie .venv..."
  python3 -m venv .venv
fi

PYTHON_BIN=".venv/bin/python"

# shellcheck disable=SC1091
source .venv/bin/activate

set -a
# shellcheck disable=SC1091
source "${ENV_FILE}"
set +a

if [[ ! -f "${PRECHECK_SCRIPT}" ]]; then
  echo "Blad: brak skryptu preflight ${PRECHECK_SCRIPT}." >&2
  exit 1
fi

precheck_status=0
"${PYTHON_BIN}" "${PRECHECK_SCRIPT}" --env-file "${ENV_FILE}" || precheck_status=$?

if [[ "${precheck_status}" -eq 1 ]]; then
  if [[ -t 0 ]]; then
    printf "System testowy nie jest w pelni uruchomiony. Uruchomic go teraz? [t/N] "
    read -r answer
    if [[ "${answer,,}" == "t" ]]; then
      ./ctiptest
      sleep 3
      "${PYTHON_BIN}" "${PRECHECK_SCRIPT}" --env-file "${ENV_FILE}" || true
    fi
  else
    echo "Uwaga: system testowy nie jest w pelni uruchomiony, ale skrypt pracuje bez TTY i nie moze zadac pytania." >&2
  fi
fi

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
