#!/usr/bin/env bash
# Control rápido de Phoenix.
# Uso: phoenix start|stop|restart|status|logs|dashboard|dashboard-stop|report|open
set -euo pipefail

SOURCE="${BASH_SOURCE[0]}"
while [[ -L "$SOURCE" ]]; do
    DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
done
REPO_DIR="$(cd -P "$(dirname "$SOURCE")/.." && pwd)"
cd "$REPO_DIR"

case "${1:-status}" in
    start)
        systemctl --user start phoenix-bot.service
        systemctl --user start phoenix-dashboard.service
        sleep 2
        systemctl --user --no-pager --lines=5 status phoenix-bot.service
        echo "Dashboard: http://127.0.0.1:8502"
        ;;
    stop)
        systemctl --user stop phoenix-dashboard.service 2>/dev/null || true
        systemctl --user stop phoenix-bot.service
        echo "Phoenix detenido."
        ;;
    restart)
        systemctl --user restart phoenix-bot.service
        systemctl --user restart phoenix-dashboard.service
        sleep 2
        systemctl --user --no-pager --lines=8 status phoenix-bot.service
        ;;
    status)
        systemctl --user --no-pager --lines=8 status phoenix-bot.service || true
        echo "--- Dashboard ---"
        systemctl --user --no-pager --lines=5 status phoenix-dashboard.service || true
        echo "--- Estado ---"
        python3 - <<'PY'
import json
from pathlib import Path

state = Path("bot/dashboard/bot_state.json")
metrics = Path("data/metrics.json")
p = state if state.exists() else metrics
if p.exists():
    d = json.loads(p.read_text())
    print("balance:", d.get("balance"))
    print("equity:", d.get("equity"))
    print("posiciones:", d.get("positions") or "sin posición")
    print("riesgo:", d.get("risk"))
else:
    print("sin estado disponible")
PY
        ;;
    logs)
        journalctl --user -u phoenix-bot -f
        ;;
    dashboard)
        systemctl --user start phoenix-dashboard.service
        sleep 2
        echo "Dashboard: http://127.0.0.1:8502"
        ;;
    dashboard-stop)
        systemctl --user stop phoenix-dashboard.service
        echo "Dashboard detenido."
        ;;
    open)
        exec "$REPO_DIR/scripts/phoenix_workspace.sh"
        ;;
    report)
        python3 - <<'PY'
import json
import sqlite3
from pathlib import Path

m = Path("data/metrics.json")
if m.exists():
    d = json.loads(m.read_text())
    print(f"Balance: ${d.get('balance', 0):.2f}")
    print(f"Equity:  ${d.get('equity', 0):.2f}")
    print(f"Posiciones: {len(d.get('positions', []))}")
    print(f"Riesgo: {d.get('risk')}")

db = Path("data/bot_history.db")
if db.exists():
    con = sqlite3.connect(db)
    try:
        cur = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(pnl_usdt), 0) FROM trades"
        )
        n, pnl = cur.fetchone()
        print(f"Trades cerrados: {n} | PnL neto: {pnl:+.4f} USDT")
        cur = con.execute(
            "SELECT symbol, side, exit_time, pnl_usdt, close_reason "
            "FROM trades ORDER BY id DESC LIMIT 5"
        )
        print("Últimos trades:")
        for row in cur.fetchall():
            print("  ", row)
    finally:
        con.close()
PY
        ;;
    *)
        echo "Uso: phoenix start|stop|restart|status|logs|dashboard|dashboard-stop|report|open"
        exit 1
        ;;
esac
