#!/usr/bin/env bash
# Abre/adjunta un workspace de terminales para Phoenix usando screen.
# Atajos: Ctrl+A luego D = salir; screen -r phoenix = volver.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SESSION="phoenix"

if ! command -v screen >/dev/null 2>&1; then
    echo "screen no está instalado. Instalalo con: sudo apt install screen"
    exec bash -lc "cd '$REPO_DIR' && exec bash"
fi

if screen -ls 2>/dev/null | grep -q "\.$SESSION"; then
    exec screen -r "$SESSION"
fi

screen -dmS "$SESSION" -t monitor bash -lc \
    "journalctl --user -u phoenix-bot -f"
screen -S "$SESSION" -X screen -t dashboard bash -lc \
    "echo 'Dashboard: http://127.0.0.1:8502'; echo; systemctl --user status phoenix-dashboard --no-pager || true; exec bash"
screen -S "$SESSION" -X screen -t shell bash -lc \
    "cd '$REPO_DIR' && exec bash"
screen -S "$SESSION" -X select 0

exec screen -r "$SESSION"
