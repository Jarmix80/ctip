#!/usr/bin/env bash
set -euo pipefail

WORKDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "${WORKDIR}/../../.." && pwd)"
ARCHIVE="${1:-${REPO_ROOT}/inbox/test_archive/20260722_before_prod_mirror}"
ENV_FILE="${ENV_FILE:-${WORKDIR}/.env.test}"
COMPOSE=(docker compose --project-directory "${WORKDIR}" --env-file "${ENV_FILE}" -f "${WORKDIR}/compose.test.yml")

if [[ ! -f "${ARCHIVE}/SHA256SUMS" ]]; then
    echo "Nie znaleziono kompletnego archiwum: ${ARCHIVE}." >&2
    exit 1
fi

(cd "${ARCHIVE}" && sha256sum --check SHA256SUMS)
"${COMPOSE[@]}" down --remove-orphans

if docker ps -a --format '{{.Names}}' | grep -Fxq ctip-postgres; then
    docker start ctip-postgres >/dev/null
fi

if [[ -f "${ARCHIVE}/menadzer_serwisu_before.fdb" ]]; then
    cp --reflink=auto --preserve=timestamps \
        "${ARCHIVE}/menadzer_serwisu_before.fdb" \
        "${REPO_ROOT}/inbox/firebird/menadzer_serwisu.fdb"
fi

echo "Przywrócono poprzednie usługi danych. Bieżący checkout kodu nie był modyfikowany."
