"""
Estado compartido entre el Bot y el Dashboard.
Se guarda en un archivo JSON para lectura/escritura entre procesos.
"""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Ruta fija para el archivo de estado dentro de la carpeta dashboard
STATE_FILE = Path(__file__).parent / "bot_state.json"


class BotState:
    """Gestiona el estado del bot compartido con el dashboard (thread-safe)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = self._default_state()
        self._last_mtime: float = 0.0
        self._load()  # Carga el estado anterior si existe, si no crea uno nuevo

    def _default_state(self) -> dict[str, Any]:
        return {
            "bot_running": False,
            "mode": "STOPPED",
            "symbol": "XRPUSDT",
            "testnet": True,
            "fase_actual": 0,
            "current_price": 0.0,
            "balance": 0.0,
            "unrealised_pnl_pct": 0.0,
            "unrealised_pnl_usdt": 0.0,
            "equity": 0.0,
            "daily_pnl": 0.0,
            "position": {"active": False, "type": None, "entry_price": 0.0, "current_sl": 0.0, "tp1": 0.0, "tp2": 0.0},
            "indicators": {"rsi_15m": 0.0, "adx_1h": 0.0, "ma_spread": 0.0, "market_ranging": False},
            "risk": {"stops_dia": 0, "max_stops_dia": 4, "session_active": True, "drawdown_pct": 0.0},
            "last_signal": {"type": None, "reason": "", "score": 0, "timestamp": None},
            "uptime_seconds": 0,
            "active_symbols": 0,
            "symbols": {},
            "errors": [],
            "equity_history": [],
            "last_update": "",
        }

    def update(self, updates: dict[str, Any]) -> None:
        with self._lock:
            self._state.update(updates)
            self._state["last_update"] = datetime.now(timezone.utc).isoformat()

    def set_position(self, pos: dict[str, Any]) -> None:
        with self._lock:
            self._state["position"].update(pos)
            self._state["last_update"] = datetime.now(timezone.utc).isoformat()

    def set_indicators(self, ind_15m: dict[str, Any], ind_1h: dict[str, Any], market_ranging: bool = False) -> None:
        with self._lock:
            self._state["indicators"] = {
                "rsi_15m": ind_15m.get("RSI", 0),
                "adx_1h": ind_1h.get("ADX", 0),
                "ma_spread": ind_1h.get("MA_spread", 0),
                "market_ranging": market_ranging,
            }
            self._state["last_update"] = datetime.now(timezone.utc).isoformat()

    def set_signal(self, signal_type: Optional[str], reason: str = "", score: int = 0) -> None:
        with self._lock:
            self._state["last_signal"] = {
                "type": signal_type,
                "reason": reason,
                "score": score,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._state["last_update"] = datetime.now(timezone.utc).isoformat()

    def add_error(self, msg: str) -> None:
        with self._lock:
            self._state["errors"].append({
                "message": msg[:200],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            # Mantener solo últimos 50
            self._state["errors"] = self._state["errors"][-50:]

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            self._reload_if_changed()
            import copy
            return copy.deepcopy(self._state)

    def _reload_if_changed(self) -> None:
        """Recarga el JSON si otro proceso (el bot) lo actualizó."""
        try:
            if not STATE_FILE.exists():
                return
            mtime = STATE_FILE.stat().st_mtime
            if mtime <= self._last_mtime:
                return
            with open(STATE_FILE, 'r') as f:
                saved = json.load(f)
            self._state.update(saved)
            self._last_mtime = mtime
        except Exception:
            pass

    def flush(self) -> None:
        """Guarda el estado a disco inmediatamente."""
        self._save()

    def _load(self) -> None:
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, 'r') as f:
                    saved = json.load(f)
                with self._lock:
                    self._state.update(saved)
                self._last_mtime = STATE_FILE.stat().st_mtime
            except Exception:
                pass

    def _save(self) -> None:
        try:
            temp = STATE_FILE.with_suffix(".tmp")
            with open(temp, 'w') as f:
                json.dump(self._state, f, indent=2, default=str)
            os.replace(temp, STATE_FILE)
        except Exception:
            pass


# Instancia global (singleton)
bot_state = BotState()