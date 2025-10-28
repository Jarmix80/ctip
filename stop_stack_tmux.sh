#!/usr/bin/env bash
set -euo pipefail

SESSION_NAME="ctip-stack"

if ! command -v tmux >/dev/null 2>&1; then
    echo "tmux nie jest zainstalowany." >&2
    exit 1
fi

if ! tmux has-session -t "${SESSION_NAME}" 2>/dev/null; then
    echo "Sesja '${SESSION_NAME}' nie istnieje." >&2
    exit 0
fi

echo "Wybierz proces do zakończenia (1-collector, 2-uvicorn, 3-sms-sender, 4-wszystko):"
read -r choice

case "$choice" in
    1)
        tmux kill-pane -t "${SESSION_NAME}:collector"
        ;;
    2)
        tmux kill-pane -t "${SESSION_NAME}:uvicorn"
        ;;
    3)
        tmux kill-pane -t "${SESSION_NAME}:sms-sender"
        ;;
    4)
        tmux kill-session -t "${SESSION_NAME}"
        ;;
    *)
        echo "Nieznany wybór." >&2
        exit 1
        ;;
esac

echo "Zakończono wybrany proces." >&2
