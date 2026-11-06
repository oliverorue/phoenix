"""Normalización única de intervalos para Binance.

El proyecto configura los timeframes como ``"15"`` y ``"60"``, pero Binance
espera ``"15m"`` y ``"1h"``. Este módulo es la única fuente de conversión.
"""

from __future__ import annotations

# Alias numéricos usados por la configuración del bot.
_ALIASES: dict[str, str] = {
    "1": "1m",
    "3": "3m",
    "5": "5m",
    "15": "15m",
    "30": "30m",
    "60": "1h",
    "120": "2h",
    "240": "4h",
    "360": "6h",
    "480": "8h",
    "720": "12h",
    "1440": "1d",
    "4320": "3d",
    "10080": "1w",
}

_VALID = {
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M",
}

_MINUTES: dict[str, int] = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480,
    "12h": 720, "1d": 1440, "3d": 4320, "1w": 10080,
    "1M": 43200,
}


def normalize_interval(interval: str | int) -> str:
    """Convierte un intervalo a la forma que acepta Binance.

    Examples
    --------
    >>> normalize_interval("15")
    '15m'
    >>> normalize_interval("60")
    '1h'
    >>> normalize_interval("60m")
    '1h'
    >>> normalize_interval("1h")
    '1h'
    """
    raw = str(interval).strip()
    if raw == "1M":  # mes: conservar mayúscula
        return "1M"

    key = raw.lower()
    if key in _ALIASES:
        return _ALIASES[key]
    if key in _VALID:
        return key

    # "60m" -> "1h", "15m" -> "15m"
    if key.endswith("m") and key[:-1].isdigit():
        minutes = key[:-1]
        if minutes in _ALIASES:
            return _ALIASES[minutes]
        return key

    return key


def interval_minutes(interval: str | int) -> int:
    """Devuelve la duración del intervalo en minutos."""
    normalized = normalize_interval(interval)
    return _MINUTES.get(normalized, 15)
