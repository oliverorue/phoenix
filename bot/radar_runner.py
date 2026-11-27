"""Motor headless del radar: scanner + paper/live sin Streamlit.

Corre como servicio systemd 24/7. Toma el lock exclusivo de ``radar.lock``,
escribe un snapshot de estado para el dashboard y consume comandos (abrir,
kill switch, auto on/off, notificar) desde ``data/radar_cmds``.
"""

from __future__ import annotations

import signal
import time
from typing import Any

from loguru import logger

from bot import radar_state
from bot.dashboard.scanner_bridge import ScannerBridge
from bot.scanner.models import Signal

_STOP = False


def _handle_signal(_signum: int, _frame: Any) -> None:
    global _STOP
    _STOP = True


def build_state(bridge: ScannerBridge) -> dict[str, Any]:
    _, stats, tracked = bridge.snapshot()
    ranked = bridge.top_signals(limit=10)
    return {
        "ts": time.time(),
        "running": bridge.is_running(),
        "dry_run": bridge.is_dry_run(),
        "auto_trade": bridge.auto_trade_enabled(),
        "halted": bridge.day_halted(),
        "halt_reason": bridge.day_halt_reason(),
        "equity": round(bridge.equity(), 4),
        "stats": bridge.paper_stats(),
        "scanner_stats": stats.model_dump(),
        "tracked": tracked,
        "last_prices": bridge.last_prices_snapshot(),
        "signals": [
            {
                "signal": item.signal.model_dump(),
                "score": item.score,
                "breakdown": item.breakdown,
            }
            for item in ranked
        ],
    }


def handle_command(bridge: ScannerBridge, command: dict[str, Any]) -> None:
    action = command.get("cmd")
    if action == "kill":
        n = bridge.kill_switch()
        logger.info(f"[motor] kill switch: {n} trade(s) cerrado(s)")
    elif action == "auto_on":
        bridge.set_auto_trade(True)
    elif action == "auto_off":
        bridge.set_auto_trade(False)
    elif action == "notify":
        bridge.notify_status()
    elif action == "open":
        signal = Signal.model_validate(command.get("signal") or {})
        trade = bridge.execute(signal)
        if trade is not None:
            logger.info(f"[motor] trade {trade.id} abierto: {trade.symbol}")
        else:
            logger.warning(f"[motor] no se pudo abrir {signal.symbol}")


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    bridge = ScannerBridge()
    if not bridge.start():
        logger.error("[motor] lock ocupado por otro proceso; saliendo")
        return 1

    logger.info("[motor] headless arriba")
    try:
        while not _STOP:
            for path, command in radar_state.drain_commands():
                try:
                    handle_command(bridge, command)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[motor] comando {path.name}: {exc}")
                finally:
                    try:
                        path.unlink()
                    except OSError:
                        pass
            try:
                radar_state.write_state(build_state(bridge))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[motor] snapshot: {exc}")
            time.sleep(1.0)
    finally:
        bridge.stop()
        logger.info("[motor] detenido")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
