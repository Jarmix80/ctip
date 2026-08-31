#!/usr/bin/env bash
set -euo pipefail

if docker compose version >/dev/null 2>&1; then
  exec docker compose "$@"
fi

for plugin in \
  /usr/libexec/docker/cli-plugins/docker-compose \
  /usr/local/lib/docker/cli-plugins/docker-compose; do
  if [[ -x "$plugin" ]]; then
    exec "$plugin" "$@"
  fi
done

echo "Nie znaleziono wtyczki Docker Compose." >&2
exit 1
