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
        echo "Monitor iniciado. Dashboard: http://127.0.0.1:8502"
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
        echo "--- Modo ---"
        echo "Monitor-only: el bot no ejecuta órdenes."
        echo "Señal puntual: python3 -m bot.monitor --once"
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
        echo "Modo monitor-only: señales y resultados del radar."
        python3 - <<'PY'
import sqlite3
from pathlib import Path

db = Path("data/scanner_signals.db")
if not db.exists():
    print("Todavía no hay señales persistidas (data/scanner_signals.db).")
else:
    con = sqlite3.connect(db)
    try:
        n = con.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        won = con.execute("SELECT COUNT(*) FROM signals WHERE outcome='WIN'").fetchone()[0]
        lost = con.execute("SELECT COUNT(*) FROM signals WHERE outcome='LOSS'").fetchone()[0]
        pnl = con.execute(
            "SELECT COALESCE(SUM(pnl_usdt), 0) FROM signals "
            "WHERE outcome IN ('WIN','LOSS')"
        ).fetchone()[0]
        closed = won + lost
        wr = (won / closed * 100) if closed else 0
        print(f"Señales: {n} | WIN {won} | LOSS {lost} | Win rate {wr:.0f}% | PnL {pnl:+.2f} USDT")
    finally:
        con.close()
PY
        ;;
    *)
        echo "Uso: phoenix start|stop|restart|status|logs|dashboard|dashboard-stop|report|open"
        exit 1
        ;;
esac
