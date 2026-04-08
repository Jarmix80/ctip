#!/usr/bin/env bash
set -euo pipefail

SESSION_NAME="${SESSION_NAME:-ctip-public-forms}"
WORKDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-${WORKDIR}/.env}"
VENV_DIR="${WORKDIR}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python"
UVICORN_BIN="${VENV_DIR}/bin/uvicorn"
PUBLIC_FORMS_HOST="${PUBLIC_FORMS_HOST:-127.0.0.1}"
PUBLIC_FORMS_PORT="${PUBLIC_FORMS_PORT:-8100}"
ALLOW_PRODUCTION_START="${ALLOW_PRODUCTION_START:-false}"

read_env_value() {
    local key="$1"
    grep -E "^${key}=" "${ENV_FILE}" | tail -n 1 | cut -d '=' -f 2- || true
}

if [[ "${ALLOW_PRODUCTION_START,,}" != "true" ]]; then
    cat >&2 <<MSG
Start produkcyjny publicznych formularzy zostal zablokowany domyslnie.
Ta sciezka sluzy tylko do jawnego wystawienia formularzy poza LAN.

Jesli swiadomie chcesz uruchomic publiczna subdomene formularzy, potwierdz to jawnie:
  ALLOW_PRODUCTION_START=true ./run_public_forms_tmux.sh

Przed startem ustaw w .env:
  FORM_PUBLIC_BASE_URL=https://form.twoja-domena.pl
MSG
    exit 1
fi

if ! command -v tmux >/dev/null 2>&1; then
    echo "tmux nie jest zainstalowany. Zainstaluj pakiet tmux i sproboj ponownie." >&2
    exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "Brak pliku srodowiskowego: ${ENV_FILE}" >&2
    exit 1
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
    echo "Nie znaleziono interpretera w ${PYTHON_BIN}. Aktywuj lub utworz .venv." >&2
    exit 1
fi

if [[ ! -x "${UVICORN_BIN}" ]]; then
    echo "Nie znaleziono binarki uvicorn (${UVICORN_BIN}). Zainstaluj zaleznosci w .venv." >&2
    exit 1
fi

if tmux has-session -t "${SESSION_NAME}" 2>/dev/null; then
    echo "Sesja tmux '${SESSION_NAME}' juz istnieje. Uzyj 'tmux attach -t ${SESSION_NAME}'." >&2
    exit 1
fi

FORM_PUBLIC_BASE_URL_VALUE="$(read_env_value "FORM_PUBLIC_BASE_URL")"
if [[ -z "${FORM_PUBLIC_BASE_URL_VALUE}" ]]; then
    echo "Brak FORM_PUBLIC_BASE_URL w ${ENV_FILE}. Publiczny formularz musi miec jawny adres HTTPS." >&2
    exit 1
fi
if [[ ! "${FORM_PUBLIC_BASE_URL_VALUE}" =~ ^https:// ]]; then
    echo "FORM_PUBLIC_BASE_URL musi zaczynac sie od https:// (aktualnie: ${FORM_PUBLIC_BASE_URL_VALUE})." >&2
    exit 1
fi
if [[ "${FORM_PUBLIC_BASE_URL_VALUE}" =~ localhost|127\.0\.0\.1 ]]; then
    echo "FORM_PUBLIC_BASE_URL nie moze wskazywac na localhost ani 127.0.0.1 przy starcie publicznym." >&2
    exit 1
fi

public_cmd="cd '${WORKDIR}' && source '${VENV_DIR}/bin/activate' && set -a && source '${ENV_FILE}' && set +a && '${UVICORN_BIN}' app.public_forms_app:app --host ${PUBLIC_FORMS_HOST} --port ${PUBLIC_FORMS_PORT}"

tmux new-session -d -s "${SESSION_NAME}" "bash -lc '${public_cmd}'"
tmux rename-window -t "${SESSION_NAME}:0" "forms-public"

cat <<INFO
Uruchomiono sesje tmux '${SESSION_NAME}' dla publicznych formularzy:
  - uvicorn (app.public_forms_app:app --host ${PUBLIC_FORMS_HOST} --port ${PUBLIC_FORMS_PORT})
  - FORM_PUBLIC_BASE_URL=${FORM_PUBLIC_BASE_URL_VALUE}

Ta aplikacja udostepnia tylko:
  - /
  - /health
  - /formularz/{token}

Dolacz do sesji:
  tmux attach -t ${SESSION_NAME}
INFO
