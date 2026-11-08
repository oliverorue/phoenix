"""Validaciones de seguridad para pasar a ejecución live."""

from __future__ import annotations

from config.loader import API_KEY, API_SECRET


class LiveConfigError(RuntimeError):
    """Configuración insuficiente o insegura para operar live."""


def enforce_live_config() -> None:
    """Exige API keys de Binance en ``secrets.env`` antes de operar live.

    Nunca se aceptan keys en la UI ni en el código: solo desde el entorno.
    """
    if not API_KEY or not API_SECRET:
        raise LiveConfigError(
            "Modo live requiere BINANCE_API_KEY y BINANCE_API_SECRET en "
            "config/secrets.env (nunca en la UI)."
        )
