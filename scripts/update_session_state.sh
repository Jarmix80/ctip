#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
STATE_FILE="${REPO_ROOT}/docs/session_state.md"
NOTE="${*:-}"
NOTE="${NOTE//$'\n'/ }"

ensure_state_file() {
    if [[ -f "${STATE_FILE}" ]]; then
        return
    fi

    cat >"${STATE_FILE}" <<'EOF'
# Stan Sesji Codex

## Biezacy Kontekst
- Biezaca galaz: (uzupelnij recznie)
- Biezace zadanie: (uzupelnij recznie)
- Co zostalo zmienione: (uzupelnij recznie)
- Co pozostalo do zrobienia: (uzupelnij recznie)
- Ostatni znany status testow: (uzupelnij recznie)
- Dokladny nastepny krok: (uzupelnij recznie)

## Historia Snapshotow

EOF
}

append_snapshot() {
    local timestamp branch status_text commits_text
    timestamp="$(date '+%Y-%m-%d %H:%M:%S %Z')"
    branch="$(git -C "${REPO_ROOT}" branch --show-current 2>/dev/null || echo "nieznana")"
    status_text="$(git -C "${REPO_ROOT}" status --short 2>/dev/null || true)"
    commits_text="$(git -C "${REPO_ROOT}" log --oneline -20 2>/dev/null || true)"

    {
        echo ""
        echo "### Snapshot ${timestamp}"
        echo "- Data/czas: \`${timestamp}\`"
        echo "- Galaz: \`${branch}\`"
        if [[ -n "${NOTE}" ]]; then
            echo "- Notatka: ${NOTE}"
        fi
        echo ""
        echo "#### git status --short"
        echo '```text'
        if [[ -n "${status_text}" ]]; then
            printf '%s\n' "${status_text}"
        else
            echo "(brak zmian)"
        fi
        echo '```'
        echo ""
        echo "#### Ostatnie 20 commitow"
        echo '```text'
        if [[ -n "${commits_text}" ]]; then
            printf '%s\n' "${commits_text}"
        else
            echo "(brak danych)"
        fi
        echo '```'
    } >>"${STATE_FILE}"
}

ensure_state_file
append_snapshot

echo "Dopisano snapshot sesji do: ${STATE_FILE}"
