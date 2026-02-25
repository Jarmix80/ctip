#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="${CONTAINER_NAME:-ctip-inbox-samba}"
SHARE_NAME="${SHARE_NAME:-ctip-inbox}"
WORKDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHARE_PATH="${WORKDIR}/inbox"
CRED_FILE="${SHARE_PATH}/.smb_credentials"

print_usage() {
    cat <<'USAGE'
Uzycie:
  ./scripts/inbox_samba.sh start
  ./scripts/inbox_samba.sh status
  ./scripts/inbox_samba.sh logs
  ./scripts/inbox_samba.sh stop
  ./scripts/inbox_samba.sh creds
USAGE
}

ensure_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Brak wymaganego polecenia: $1" >&2
        exit 1
    fi
}

load_or_create_creds() {
    local generated_password
    local default_user="ctipdrop"

    mkdir -p "${SHARE_PATH}"

    if [[ -n "${INBOX_SMB_USER:-}" && -n "${INBOX_SMB_PASSWORD:-}" ]]; then
        :
    elif [[ -f "${CRED_FILE}" ]]; then
        # shellcheck disable=SC1090
        source "${CRED_FILE}"
        INBOX_SMB_USER="${INBOX_SMB_USER:-$default_user}"
        INBOX_SMB_PASSWORD="${INBOX_SMB_PASSWORD:-}"
    else
        INBOX_SMB_USER="${default_user}"
        generated_password="$(
            printf '%s%s%s' "${RANDOM}" "${RANDOM}" "$(date +%s%N)" \
            | sha256sum \
            | awk '{print $1}' \
            | cut -c1-20
        )"
        INBOX_SMB_PASSWORD="${generated_password}"
    fi

    if [[ -z "${INBOX_SMB_PASSWORD:-}" ]]; then
        echo "Brak hasla SMB. Ustaw INBOX_SMB_PASSWORD lub usun ${CRED_FILE} i uruchom start ponownie." >&2
        exit 1
    fi

    cat >"${CRED_FILE}" <<EOF
INBOX_SMB_USER=${INBOX_SMB_USER}
INBOX_SMB_PASSWORD=${INBOX_SMB_PASSWORD}
EOF
    chmod 600 "${CRED_FILE}"
}

start_share() {
    ensure_cmd docker
    load_or_create_creds

    docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

    docker run -d \
        --name "${CONTAINER_NAME}" \
        --restart unless-stopped \
        -p 139:139 \
        -p 445:445 \
        -v "${SHARE_PATH}:/shared/inbox" \
        dperson/samba \
        -p \
        -n \
        -u "${INBOX_SMB_USER};${INBOX_SMB_PASSWORD}" \
        -s "${SHARE_NAME};/shared/inbox;yes;no;no;${INBOX_SMB_USER};${INBOX_SMB_USER}" >/dev/null

    echo "Udzial SMB uruchomiony."
    echo "Sciezka: \\\\$(hostname -I | awk '{print $1}')\\${SHARE_NAME}"
    echo "Uzytkownik: ${INBOX_SMB_USER}"
    echo "Haslo: ${INBOX_SMB_PASSWORD}"
    echo "Plik z danymi: ${CRED_FILE}"
}

show_status() {
    ensure_cmd docker
    docker ps --filter "name=${CONTAINER_NAME}" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
}

show_logs() {
    ensure_cmd docker
    docker logs --tail 100 "${CONTAINER_NAME}"
}

stop_share() {
    ensure_cmd docker
    docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
    echo "Udzial SMB zatrzymany."
}

show_creds() {
    if [[ ! -f "${CRED_FILE}" ]]; then
        echo "Brak pliku z danymi dostepowymi: ${CRED_FILE}" >&2
        exit 1
    fi
    # shellcheck disable=SC1090
    source "${CRED_FILE}"
    echo "Sciezka: \\\\$(hostname -I | awk '{print $1}')\\${SHARE_NAME}"
    echo "Uzytkownik: ${INBOX_SMB_USER}"
    echo "Haslo: ${INBOX_SMB_PASSWORD}"
}

case "${1:-}" in
    start) start_share ;;
    status) show_status ;;
    logs) show_logs ;;
    stop) stop_share ;;
    creds) show_creds ;;
    *) print_usage; exit 1 ;;
esac
