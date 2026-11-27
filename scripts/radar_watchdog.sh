#!/usr/bin/env bash
# Watchdog del motor headless: reinicia si el heartbeat quedó viejo.
set -euo pipefail

STATE="${RADAR_STATE:-/opt/phoenix/data/radar_state.json}"
MAX_AGE="${RADAR_MAX_AGE:-90}"
SERVICE="${RADAR_SERVICE:-phoenix-radar-engine.service}"

AGE="$(python3 - "$STATE" <<'PY'
import json
import sys
import time

try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        ts = float(json.load(fh).get("ts", 0.0))
except Exception:
    print(10**9)
else:
    print(int(time.time() - ts))
PY
)"

if [ "$AGE" -gt "$MAX_AGE" ]; then
    echo "heartbeat viejo (${AGE}s > ${MAX_AGE}s): reiniciando ${SERVICE}"
    systemctl restart "$SERVICE"
else
    echo "heartbeat ok (${AGE}s)"
fi
