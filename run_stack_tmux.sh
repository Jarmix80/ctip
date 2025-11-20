#!/usr/bin/env bash
set -euo pipefail

SESSION_NAME="ctip-stack"
WORKDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${WORKDIR}/.env"
VENV_DIR="${WORKDIR}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python"
UVICORN_BIN="${VENV_DIR}/bin/uvicorn"

if ! command -v tmux >/dev/null 2>&1; then
    echo "tmux nie jest zainstalowany. Zainstaluj pakiet tmux i spróbuj ponownie." >&2
    exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "Brak pliku .env w ${WORKDIR}. Upewnij się, że konfiguracja środowiska jest gotowa." >&2
    exit 1
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
    echo "Nie znaleziono interpretera w ${PYTHON_BIN}. Aktywuj lub utwórz wirtualne środowisko (.venv)." >&2
    exit 1
fi

if [[ ! -x "${UVICORN_BIN}" ]]; then
    echo "Nie znaleziono binarki uvicorn (${UVICORN_BIN}). Zainstaluj zależności w .venv (pip install -r requirements.txt)." >&2
    exit 1
fi

if tmux has-session -t "${SESSION_NAME}" 2>/dev/null; then
    echo "Sesja tmux '${SESSION_NAME}' już istnieje. Użyj 'tmux attach -t ${SESSION_NAME}' lub zakończ ją przed ponownym uruchomieniem." >&2
    exit 1
fi

collect_cmd="cd '${WORKDIR}' && set -a && source '${ENV_FILE}' && set +a && '${PYTHON_BIN}' -u collector_full.py"
uvicorn_cmd="cd '${WORKDIR}' && set -a && source '${ENV_FILE}' && set +a && '${UVICORN_BIN}' app.main:app --reload --host 0.0.0.0 --port 8000"
sender_cmd="cd '${WORKDIR}' && set -a && source '${ENV_FILE}' && set +a && '${PYTHON_BIN}' -u sms_sender.py"

# okno 0 – collector
tmux new-session -d -s "${SESSION_NAME}" "bash -lc '${collect_cmd}'"
tmux rename-window -t "${SESSION_NAME}:0" "collector"

# okno 1 – uvicorn
tmux new-window -t "${SESSION_NAME}:1" -n "uvicorn" "bash -lc '${uvicorn_cmd}'"

# okno 2 – sms sender
tmux new-window -t "${SESSION_NAME}:2" -n "sms-sender" "bash -lc '${sender_cmd}'"

echo "Uruchomiono sesję tmux '${SESSION_NAME}' z trzema oknami:"
echo "  - collector (collector_full.py)"
echo "  - uvicorn   (uvicorn app.main:app --reload)"
echo "  - sms-sender (sms_sender.py)"
echo
echo "Dołącz do sesji: tmux attach -t ${SESSION_NAME}"
