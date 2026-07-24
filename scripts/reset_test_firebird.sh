#!/usr/bin/env bash
set -euo pipefail

WORKDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-${WORKDIR}/.env.test}"
BASE_FILE="${WORKDIR}/runtime/firebird/BAZAMS_TEST_BASE.FDB"
WORKING_FILE="${WORKDIR}/runtime/firebird/BAZAMS_TEST.FDB"
COMPOSE=(docker compose --project-directory "${WORKDIR}" --env-file "${ENV_FILE}" -f "${WORKDIR}/compose.test.yml")

if [[ ! -f "${BASE_FILE}" ]]; then
    echo "Brak bazowego snapshotu ${BASE_FILE}." >&2
    exit 1
fi

"${COMPOSE[@]}" stop web firebird >/dev/null 2>&1 || true
rm -f "${WORKING_FILE}"
cp --reflink=auto --preserve=timestamps "${BASE_FILE}" "${WORKING_FILE}"
chmod 0660 "${WORKING_FILE}"
"${COMPOSE[@]}" up -d firebird web
echo "Przywrócono roboczą bazę Firebird z lokalnego snapshotu bazowego."
