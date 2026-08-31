#!/usr/bin/env bash
set -euo pipefail

WORKDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Tryb tmux został zastąpiony jednym stosem Docker Compose ctip-test." >&2
exec "${WORKDIR}/ctiptest" start
