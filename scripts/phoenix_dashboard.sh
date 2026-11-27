#!/usr/bin/env bash
# Abre el dashboard de Phoenix en un solo paso: túnel SSH + navegador.
# Uso: ./scripts/phoenix_dashboard.sh   (o el alias `phx`)
set -euo pipefail

HOST="${PHOENIX_VPS:-root@216.238.124.144}"
PORT="${PHOENIX_DASH_PORT:-8502}"
URL="http://localhost:${PORT}"
HEALTH="${URL}/_stcore/health"

open_url() {
    [[ "${PHOENIX_NO_BROWSER:-0}" == "1" ]] && return 0
    if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$URL" >/dev/null 2>&1 &
    elif command -v open >/dev/null 2>&1; then
        open "$URL" >/dev/null 2>&1 &
    elif [[ -n "${BROWSER:-}" ]]; then
        "$BROWSER" "$URL" >/dev/null 2>&1 &
    else
        echo "Abrí manualmente: $URL"
    fi
}

if curl -fsS --max-time 2 "$HEALTH" >/dev/null 2>&1; then
    echo "Dashboard ya disponible en $URL"
    open_url
    exit 0
fi

echo "Abriendo túnel a $HOST (Ctrl+C para cerrar)..."
( sleep 2; open_url ) &
exec ssh -N \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ConnectTimeout=10 \
    -L "${PORT}:127.0.0.1:${PORT}" "$HOST"
