"""
Configuración del scanner global (pydantic v2).

Los valores por defecto están calibrados para un entorno de 8 GB de RAM con
ZRAM: streams combinados (no un socket por símbolo), ventanas acotadas y
colas con límite. Se pueden sobreescribir desde ``config/settings.yaml``
(sección ``scanner``) vía :meth:`ScannerConfig.from_settings`.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from config.loader import SCANNER_CFG


class ScannerConfig(BaseModel):
    # ── Conectividad ─────────────────────────────────────
    use_mainnet: bool = True  # false → wss://stream.binancefuture.com
    message_max_size: int = 2 * 1024 * 1024  # tope de payload por mensaje WS
    reconnect_base_seconds: float = 1.0
    reconnect_max_seconds: float = 30.0

    # ── Universo / filtrado de monedas "basura" ──────────
    min_quote_volume_24h: float = 5_000_000.0  # $5M de volumen 24h mínimo
    max_symbols: int = 60                       # techo del universo rastreado
    symbols_per_shard: int = 50                 # streams por socket combinado
    watchlist: list[str] = Field(default_factory=list)  # siempre incluidos

    # ── Book / depth ────────────────────────────────────
    depth_levels: Literal[5, 10, 20] = 20
    depth_symbols_limit: int = 30  # solo los top-N por volumen llevan depth20

    # ── Funding / polling REST ──────────────────────────
    funding_poll_seconds: int = 60

    # ── Cola de señales (backpressure) ──────────────────
    signal_queue_maxlen: int = 200

    # ── Umbrales del motor de anomalías ─────────────────
    obi_extreme: float = 0.6               # |imbalance| del book > 0.6
    volume_zscore: float = 3.0             # z-score de volumen para "spike"
    volume_delta_window: int = 60          # nº de trades en la ventana delta
    funding_extreme: float = 0.0005        # |funding| > 0.05% es extremo
    liq_cascade_window_seconds: int = 60   # ventana para liquidaciones
    liq_cascade_min_events: int = 5        # nº mínimo de forced-orders
    liq_cascade_min_notional: float = 2_000_000.0  # nocional mínimo liquidado

    @classmethod
    def from_settings(cls, overrides: Optional[dict[str, Any]] = None) -> "ScannerConfig":
        """Construye la config desde `settings.yaml` (sección `scanner`)."""
        data: dict[str, Any] = dict(SCANNER_CFG or {})
        if overrides:
            data.update(overrides)
        return cls(**data)
