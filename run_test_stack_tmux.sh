#!/usr/bin/env bash
set -euo pipefail

SESSION_NAME="ctip-stack-test"
WORKDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-${WORKDIR}/.env.test}"
VENV_DIR="${WORKDIR}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python"
UVICORN_BIN="${VENV_DIR}/bin/uvicorn"
TEST_UVICORN_PORT="${TEST_UVICORN_PORT:-8000}"
TEST_PUBLIC_HOST="${TEST_PUBLIC_HOST:-}"
PRODUCTION_DB_HOST="192.168.0.8"
PRODUCTION_PBX_HOST="192.168.0.11"
LOCAL_TEST_DATABASE="ctip_test"

read_env_value() {
    local key="$1"
    grep -E "^${key}=" "${ENV_FILE}" | tail -n 1 | cut -d '=' -f 2- || true
}

assert_env_value_not_production() {
    local key="$1"
    local production_value="$2"
    local description="$3"
    local current_value
    current_value="$(read_env_value "${key}")"
    if [[ -n "${current_value}" && "${current_value}" == "${production_value}" ]]; then
        echo "${key} wskazuje na produkcyjne ${description} (${current_value}). Środowisko testowe musi korzystać wyłącznie z lokalnych zasobów." >&2
        exit 1
    fi
}

assert_env_value_equals() {
    local key="$1"
    local expected_value="$2"
    local error_message="$3"
    local current_value
    current_value="$(read_env_value "${key}")"
    if [[ "${current_value}" != "${expected_value}" ]]; then
        echo "${error_message}" >&2
        exit 1
    fi
}

if ! command -v tmux >/dev/null 2>&1; then
    echo "tmux nie jest zainstalowany. Zainstaluj pakiet tmux i spróbuj ponownie." >&2
    exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "Brak pliku środowiskowego ${ENV_FILE}. Utwórz go na podstawie .env.test.example." >&2
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

PBX_HOST_VALUE="$(read_env_value "PBX_HOST")"
if [[ -z "${PBX_HOST_VALUE}" ]]; then
    echo "Nie określono PBX_HOST w ${ENV_FILE}." >&2
    exit 1
fi
assert_env_value_not_production "PBX_HOST" "${PRODUCTION_PBX_HOST}" "centralę CTIP"
assert_env_value_not_production "PGHOST" "${PRODUCTION_DB_HOST}" "bazę PostgreSQL"
assert_env_value_not_production "FB_HOST" "${PRODUCTION_DB_HOST}" "bazę Firebird"
assert_env_value_not_production "FB_V_HOST" "${PRODUCTION_DB_HOST}" "bazę Firebird v-maintenance"
assert_env_value_equals "PGDATABASE" "${LOCAL_TEST_DATABASE}" "PGDATABASE w ${ENV_FILE} musi mieć wartość ${LOCAL_TEST_DATABASE}, aby lokalna praca zawsze trafiała do jednej testowej bazy."
assert_env_value_equals "SMS_TEST_MODE" "true" "SMS_TEST_MODE w ${ENV_FILE} musi mieć wartość true, aby lokalne środowisko nie wysyłało realnych wiadomości."

resolve_test_public_host() {
    if [[ -n "${TEST_PUBLIC_HOST}" ]]; then
        printf '%s' "${TEST_PUBLIC_HOST}"
        return
    fi

    local detected_host
    detected_host="$(hostname -I 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i !~ /^127\\./ && $i !~ /^172\\./) { print $i; exit }}')"
    if [[ -n "${detected_host}" ]]; then
        printf '%s' "${detected_host}"
        return
    fi

    printf '127.0.0.1'
}

TEST_PUBLIC_HOST_VALUE="$(resolve_test_public_host)"
TEST_PUBLIC_BASE_URL="http://${TEST_PUBLIC_HOST_VALUE}:${TEST_UVICORN_PORT}"
TEST_ADMIN_PANEL_URL="${TEST_PUBLIC_BASE_URL}/admin"

common_env="cd '${WORKDIR}' && set -a && source '${ENV_FILE}' && set +a && export ADMIN_PANEL_URL='${TEST_ADMIN_PANEL_URL}' FORM_PUBLIC_BASE_URL='${TEST_PUBLIC_BASE_URL}' &&"
collect_cmd="${common_env} '${PYTHON_BIN}' -u collector_full.py"
uvicorn_cmd="${common_env} '${UVICORN_BIN}' app.main:app --host 0.0.0.0 --port ${TEST_UVICORN_PORT}"
sender_cmd="${common_env} '${PYTHON_BIN}' -u sms_sender.py"

# okno 0 – collector
tmux new-session -d -s "${SESSION_NAME}" "bash -lc '${collect_cmd}'"
tmux rename-window -t "${SESSION_NAME}:0" "collector"

# okno 1 – uvicorn
tmux new-window -t "${SESSION_NAME}:1" -n "uvicorn" "bash -lc '${uvicorn_cmd}'"

# okno 2 – sms sender
tmux new-window -t "${SESSION_NAME}:2" -n "sms-sender" "bash -lc '${sender_cmd}'"

cat <<INFO
Uruchomiono sesję tmux '${SESSION_NAME}' (lokalne środowisko testowe ${ENV_FILE}):
  - collector (collector_full.py -> PostgreSQL ${LOCAL_TEST_DATABASE})
  - uvicorn   (app.main:app --port ${TEST_UVICORN_PORT})
  - sms-sender (sms_sender.py w trybie SMS_TEST_MODE=true)
  - publiczny adres WWW: ${TEST_PUBLIC_BASE_URL}/
  - panel admin: ${TEST_ADMIN_PANEL_URL}

Dołącz do sesji: tmux attach -t ${SESSION_NAME}
INFO
