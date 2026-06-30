"""
Recuperación automática ante crashes — multi‑símbolo.
"""

import json
from typing import Any

from config.loader import BASE_DIR


class StateRecovery:
    """Guarda y recupera estado crítico de las posiciones (multi‑símbolo)."""
    STATE_FILE = BASE_DIR / "data" / "bot_state_persistent.json"

    @staticmethod
    def _load_all() -> dict[str, Any]:
        """Carga el archivo JSON completo o retorna dict vacío."""
        try:
            with open(StateRecovery.STATE_FILE, "r", encoding="utf-8") as f:
                return dict(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _write_all(payload: dict[str, Any]) -> bool:
        """Escribe el payload completo a disco."""
        try:
            StateRecovery.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(StateRecovery.STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, default=str)
            return True
        except Exception:
            return False

    @staticmethod
    def save_symbol_state(symbol: str, state: dict[str, Any]) -> bool:
        data = StateRecovery._load_all()
        data[symbol] = state
        return StateRecovery._write_all(data)

    @staticmethod
    def load_symbol_state(symbol: str) -> dict[str, Any]:
        data = StateRecovery._load_all()
        return data.get(symbol, {})

    @staticmethod
    def load_all_symbols() -> dict[str, dict[str, Any]]:
        return StateRecovery._load_all()

    @staticmethod
    def save_state(state: dict[str, Any]) -> bool:
        return StateRecovery._write_all(state)

    @staticmethod
    def clear_symbol(symbol: str) -> bool:
        data = StateRecovery._load_all()
        data.pop(symbol, None)
        return StateRecovery._write_all(data)

    @staticmethod
    def load_state() -> dict[str, Any]:
        """Alias legacy para load_all_symbols."""
        return StateRecovery.load_all_symbols()
