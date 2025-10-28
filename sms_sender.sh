#!/usr/bin/env bash
set -euo pipefail

SESSION_NAME="sms_sender"
WORKDIR="/home/marcin/projects/ctip"
ENV_FILE="${WORKDIR}/.env"
PYTHON_BIN="${WORKDIR}/.venv/bin/python"

# Sprawdzenie interpretera Pythona
if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN="$(command -v python3)"
fi

if [[ -z "$PYTHON_BIN" ]]; then
    echo "Nie znaleziono interpretera Python3. Zainstaluj pakiet python3 lub utwórz .venv." >&2
    exit 1
fi

# Przejście do katalogu projektu
cd "$WORKDIR"

# Sprawdzenie, czy sesja tmux już istnieje
sess_exists=$(tmux has-session -t "$SESSION_NAME" 2>/dev/null && echo "yes" || echo "no")
if [[ "$sess_exists" == "yes" ]]; then
    tmux new-session -d -s "${SESSION_NAME}_tmp" "sleep 0" 2>/dev/null || true
    echo "Sesja tmux '${SESSION_NAME}' już istnieje. Użyj 'tmux attach -t ${SESSION_NAME}' lub zakończ ją przed ponownym uruchomieniem." >&2
    exit 1
fi

# Komenda do uruchomienia
CMD="set -a && source '${ENV_FILE}' && set +a && ${PYTHON_BIN} -u sms_sender.py"

# Uruchomienie sesji tmux
tmux new-session -d -s "$SESSION_NAME" "bash -lc '$CMD'"

echo "Sesja tmux '${SESSION_NAME}' została uruchomiona." >&2
echo "Dołącz: tmux attach -t ${SESSION_NAME}" >&2
