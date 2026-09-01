#!/usr/bin/env bash
set -euo pipefail

WORKDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-${WORKDIR}/.env.test}"
VENV_DIR="${VENV_DIR:-${WORKDIR}/.venv}"
PYTHON_BIN="${VENV_DIR}/bin/python"
PIP_BIN="${VENV_DIR}/bin/pip"
UVICORN_BIN="${VENV_DIR}/bin/uvicorn"

SESSION_NAME="${SESSION_NAME:-ctip-stack-test}"
APP_HOST="${APP_HOST:-0.0.0.0}"
APP_PORT="${APP_PORT:-8000}"
UVICORN_RELOAD="${UVICORN_RELOAD:-false}"
ALLOW_PRODUCTION_START="${ALLOW_PRODUCTION_START:-false}"
PRODUCTION_DB_HOST="192.168.0.8"
PRODUCTION_PBX_HOST="192.168.0.11"
LOCAL_TEST_DATABASE="ctip_test"

FIREBIRD_CONTAINER="${FIREBIRD_CONTAINER:-ctip-firebird-local}"
START_FIREBIRD="${START_FIREBIRD:-auto}" # auto|always|never
FIREBIRD_WAIT_SECONDS="${FIREBIRD_WAIT_SECONDS:-30}"

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

if [[ "$(basename "${ENV_FILE}")" != ".env.test" ]] && [[ "${ALLOW_PRODUCTION_START,,}" != "true" ]]; then
    cat >&2 <<MSG
Start na pliku srodowiskowym '${ENV_FILE}' zostal zablokowany domyslnie.
Domyslny tryb uruchomienia calego systemu to srodowisko testowe.

Uzyj:
  ./ctiptest
  ./run_test_stack_tmux.sh
  ENV_FILE=.env.test ./run_server_with_firebird.sh

Jesli swiadomie chcesz uruchomic produkcje, potwierdz to jawnie:
  ALLOW_PRODUCTION_START=true ./run_server_with_firebird.sh

Wdrozenia produkcyjne wykonuj z commita GitHub i jawnego .env po stronie serwera.
MSG
    exit 1
fi

if ! command -v tmux >/dev/null 2>&1; then
    echo "tmux nie jest zainstalowany. Zainstaluj pakiet tmux i sproboj ponownie." >&2
    exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "Brak polecenia docker. To uruchomienie wymaga lokalnego Docker Engine." >&2
    exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "Brak pliku srodowiskowego: ${ENV_FILE}" >&2
    exit 1
fi

if [[ "$(basename "${ENV_FILE}")" == ".env.test" ]]; then
    PBX_HOST_VALUE="$(read_env_value "PBX_HOST")"
    if [[ -z "${PBX_HOST_VALUE}" ]]; then
        echo "Nie okreslono PBX_HOST w ${ENV_FILE}." >&2
        exit 1
    fi
    assert_env_value_not_production "PBX_HOST" "${PRODUCTION_PBX_HOST}" "centralę CTIP"
    assert_env_value_not_production "PGHOST" "${PRODUCTION_DB_HOST}" "bazę PostgreSQL"
    assert_env_value_not_production "FB_HOST" "${PRODUCTION_DB_HOST}" "bazę Firebird"
    assert_env_value_not_production "FB_V_HOST" "${PRODUCTION_DB_HOST}" "bazę Firebird v-maintenance"
    assert_env_value_equals "PGDATABASE" "${LOCAL_TEST_DATABASE}" "PGDATABASE w ${ENV_FILE} musi mieć wartość ${LOCAL_TEST_DATABASE}, aby lokalna praca zawsze trafiała do jednej testowej bazy."
    assert_env_value_equals "SMS_TEST_MODE" "true" "SMS_TEST_MODE w ${ENV_FILE} musi mieć wartość true, aby lokalne środowisko nie wysyłało realnych wiadomości."
fi

if [[ ! -d "${VENV_DIR}" ]]; then
    echo "Nie znaleziono .venv. Tworze nowe srodowisko..."
    python3 -m venv "${VENV_DIR}"
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
    echo "Nie znaleziono interpretera Python w ${PYTHON_BIN}." >&2
    exit 1
fi

if [[ ! -x "${UVICORN_BIN}" ]]; then
    echo "Brak uvicorn w .venv. Instaluje zaleznosci z requirements.txt..."
    "${PIP_BIN}" install -r "${WORKDIR}/requirements.txt"
fi

# shellcheck disable=SC1090
set -a && source "${ENV_FILE}" && set +a

FB_HOST="${FB_HOST:-127.0.0.1}"
FB_PORT="${FB_PORT:-3050}"
FIREBIRD_WAIT_HOST="${FB_HOST}"

is_local_firebird_host=false
case "${FB_HOST}" in
    127.0.0.1|localhost|::1)
        is_local_firebird_host=true
        ;;
esac

should_start_firebird=false
case "${START_FIREBIRD}" in
    always)
        should_start_firebird=true
        ;;
    never)
        should_start_firebird=false
        ;;
    auto)
        if [[ "${is_local_firebird_host}" == "true" ]]; then
            should_start_firebird=true
        fi
        ;;
    *)
        echo "Nieznana wartosc START_FIREBIRD=${START_FIREBIRD}. Uzyj auto|always|never." >&2
        exit 1
        ;;
esac

if [[ "${should_start_firebird}" == "true" ]]; then
    if [[ "${is_local_firebird_host}" != "true" ]]; then
        # Kontener Firebird jest lokalny, wiec sprawdzamy nasluch po localhost.
        FIREBIRD_WAIT_HOST="127.0.0.1"
    fi

    if ! docker ps -a --format '{{.Names}}' | grep -Fxq "${FIREBIRD_CONTAINER}"; then
        cat >&2 <<MSG
Nie znaleziono kontenera Firebird '${FIREBIRD_CONTAINER}'.
Utworz go raz (z aliasami BAZAMS_TEST) i uruchom skrypt ponownie.
MSG
        exit 1
    fi

    running_state="$(docker inspect -f '{{.State.Running}}' "${FIREBIRD_CONTAINER}")"
    if [[ "${running_state}" != "true" ]]; then
        echo "Uruchamiam kontener Firebird: ${FIREBIRD_CONTAINER}"
        docker start "${FIREBIRD_CONTAINER}" >/dev/null
    else
        echo "Kontener Firebird juz dziala: ${FIREBIRD_CONTAINER}"
    fi

    echo "Czekam na nasluch Firebird ${FIREBIRD_WAIT_HOST}:${FB_PORT} (max ${FIREBIRD_WAIT_SECONDS}s)..."
    firebird_ready=false
    for _ in $(seq 1 "${FIREBIRD_WAIT_SECONDS}"); do
        if nc -z "${FIREBIRD_WAIT_HOST}" "${FB_PORT}" >/dev/null 2>&1; then
            firebird_ready=true
            break
        fi
        sleep 1
    done
    if [[ "${firebird_ready}" != "true" ]]; then
        echo "Firebird nie nasluchuje na ${FIREBIRD_WAIT_HOST}:${FB_PORT}." >&2
        exit 1
    fi
fi

if tmux has-session -t "${SESSION_NAME}" 2>/dev/null; then
    echo "Sesja tmux '${SESSION_NAME}' juz istnieje. Uzyj 'tmux attach -t ${SESSION_NAME}'." >&2
    exit 1
fi

source_ip="$(hostname -I 2>/dev/null | xargs || true)"
if [[ -n "${source_ip}" ]]; then
    echo "Adres(y) hosta kolektora: ${source_ip}"
fi

common_env="cd '${WORKDIR}' && source '${VENV_DIR}/bin/activate' && set -a && source '${ENV_FILE}' && set +a &&"
collect_cmd="${common_env} '${PYTHON_BIN}' -u collector_full.py"
sender_cmd="${common_env} '${PYTHON_BIN}' -u sms_sender.py"
uvicorn_cmd="${common_env} '${UVICORN_BIN}' app.main:app --host ${APP_HOST} --port ${APP_PORT}"

if [[ "${UVICORN_RELOAD,,}" == "true" ]]; then
    uvicorn_cmd="${uvicorn_cmd} --reload"
fi

tmux new-session -d -s "${SESSION_NAME}" "bash -lc '${collect_cmd}'"
tmux rename-window -t "${SESSION_NAME}:0" "collector"
tmux new-window -t "${SESSION_NAME}:1" -n "uvicorn" "bash -lc '${uvicorn_cmd}'"
tmux new-window -t "${SESSION_NAME}:2" -n "sms-sender" "bash -lc '${sender_cmd}'"

cat <<INFO
Uruchomiono sesje tmux '${SESSION_NAME}':
  - collector  (collector_full.py -> PostgreSQL ${PGDATABASE:-nieustawione})
  - uvicorn    (app.main:app --host ${APP_HOST} --port ${APP_PORT})
  - sms-sender (sms_sender.py)

Firebird:
  - START_FIREBIRD=${START_FIREBIRD}
  - kontener=${FIREBIRD_CONTAINER}
  - FB_HOST z ENV=${FB_HOST}
  - oczekiwany nasluch=${FIREBIRD_WAIT_HOST}:${FB_PORT}

Dolacz do sesji:
  tmux attach -t ${SESSION_NAME}
INFO
