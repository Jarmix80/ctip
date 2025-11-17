#!/usr/bin/env bash
set -euo pipefail

SESSION_NAME="ctip-stack-test"
WORKDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${WORKDIR}/.env.test"
VENV_DIR="${WORKDIR}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python"
UVICORN_BIN="${VENV_DIR}/bin/uvicorn"
TEST_UVICORN_PORT="${TEST_UVICORN_PORT:-18000}"

if ! command -v tmux >/dev/null 2>&1; then
    echo "tmux nie jest zainstalowany. Zainstaluj pakiet tmux i spróbuj ponownie." >&2
    exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "Brak pliku .env.test w ${WORKDIR}. Utwórz go na podstawie .env.test.example." >&2
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

PBX_HOST_VALUE=$(grep -E '^PBX_HOST=' "${ENV_FILE}" | tail -n 1 | cut -d '=' -f 2-)
if [[ -z "${PBX_HOST_VALUE}" ]]; then
    echo "Nie określono PBX_HOST w ${ENV_FILE}." >&2
    exit 1
fi
if [[ "${PBX_HOST_VALUE}" =~ ^192\.168\.0\.11$ ]]; then
    echo "PBX_HOST wskazuje na produkcyjną centralę (${PBX_HOST_VALUE}). Środowisko testowe musi korzystać z mocka lub innej centrali." >&2
    exit 1
fi

SMS_TEST_MODE_VALUE=$(grep -E '^SMS_TEST_MODE=' "${ENV_FILE}" | tail -n 1 | cut -d '=' -f 2-)
if [[ "${SMS_TEST_MODE_VALUE,,}" != "true" ]]; then
    cat >&2 <<MSG
SMS_TEST_MODE w ${ENV_FILE} nie jest ustawione na true.
Testowe środowisko musi mieć wymuszone SMS_TEST_MODE=true, aby nie wysyłać produkcyjnych SMS.
MSG
    exit 1
fi

collect_cmd="cd '${WORKDIR}' && set -a && source '${ENV_FILE}' && set +a && '${PYTHON_BIN}' -u collector_full.py"
uvicorn_cmd="cd '${WORKDIR}' && set -a && source '${ENV_FILE}' && set +a && '${UVICORN_BIN}' app.main:app --reload --host 0.0.0.0 --port ${TEST_UVICORN_PORT}"
sender_cmd="cd '${WORKDIR}' && set -a && source '${ENV_FILE}' && set +a && '${PYTHON_BIN}' -u sms_sender.py"

# okno 0 – collector
tmux new-session -d -s "${SESSION_NAME}" "bash -lc '${collect_cmd}'"
tmux rename-window -t "${SESSION_NAME}:0" "collector"

# okno 1 – uvicorn
tmux new-window -t "${SESSION_NAME}:1" -n "uvicorn" "bash -lc '${uvicorn_cmd}'"

# okno 2 – sms sender
tmux new-window -t "${SESSION_NAME}:2" -n "sms-sender" "bash -lc '${sender_cmd}'"

cat <<INFO
Uruchomiono sesję tmux '${SESSION_NAME}' (środowisko testowe .env.test):
  - collector (collector_full.py)
  - uvicorn   (app.main:app --port ${TEST_UVICORN_PORT})
  - sms-sender (sms_sender.py)

Dołącz do sesji: tmux attach -t ${SESSION_NAME}
INFO
