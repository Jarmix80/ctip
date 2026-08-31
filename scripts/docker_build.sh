#!/usr/bin/env bash
set -euo pipefail

if docker buildx version >/dev/null 2>&1; then
    exec docker buildx build --load "$@"
fi

for candidate in \
    /usr/libexec/docker/cli-plugins/docker-buildx \
    /usr/local/lib/docker/cli-plugins/docker-buildx; do
    if [[ -x "${candidate}" ]]; then
        exec "${candidate}" build --load "$@"
    fi
done

if docker build --help 2>&1 | grep -q -- '--label'; then
    exec docker build "$@"
fi

echo "Nie znaleziono działającego polecenia Docker Buildx." >&2
exit 127
