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
    signal_cooldown_seconds: int = 120  # no re-emitir mismo símbolo+dirección

    # ── Umbrales del motor de anomalías ─────────────────
    obi_extreme: float = 0.35              # |imbalance| del book > 0.35
    volume_zscore: float = 2.0             # z-score de volumen para "spike"
    delta_extreme: float = 0.20            # |delta| > 0.20 = taker unidireccional
    volume_delta_window_seconds: int = 60  # ventana móvil del volume delta
    funding_extreme: float = 0.0003        # |funding| > 0.03% es extremo
    liq_cascade_window_seconds: int = 60   # ventana para liquidaciones
    liq_cascade_min_events: int = 3        # nº mínimo de forced-orders
    liq_cascade_min_notional: float = 2_000_000.0  # nocional mínimo liquidado

    # ── Calidad y ranking de señales ─────────────────────
    min_confidence: float = 0.40           # piso de confianza para emitir
    min_reasons: int = 2                   # nº mínimo de motivos alineados
    top_n: int = 3                         # cuántas señales mostrar (mejores)
    w_confidence: float = 0.50             # pesos del score compuesto
    w_reasons: float = 0.25
    w_liquidity: float = 0.15
    w_freshness: float = 0.10
    freshness_halflife_seconds: int = 300  # la señal se "enfría" en ~5 min

    # ── Ejecución (dry-run / live) ──────────────────────
    dry_run: bool = True  # True → paper simulado; False → órdenes reales (live)
    paper_initial_capital: float = 15.0  # capital inicial del paper (USDT)
    max_risk_pct: float = 0.10           # techo de riesgo/trade (Kelly manda hasta acá)
    max_hold_seconds: int = 7200         # tiempo máximo abierto antes de cerrar por TIMEOUT
    sl_distance_pct: float = 0.015  # distancia al SL (1R) como fracción del precio
    tp1_ratio: float = 0.6          # porción cerrada en TP1 (50-65%)
    trailing_dist_pct: float = 0.0  # 0 = sin trailing (salida en TP2)

    # ── Auto-entrada (opcional) ─────────────────────────
    auto_trade: bool = False                  # abre el top-1 automáticamente
    max_open_trades: int = 1                  # trades abiertos simultáneos
    auto_trade_interval_seconds: float = 5.0  # cada cuánto evalúa el loop
    auto_trade_max_signal_age_seconds: float = 60.0   # ignora señales viejas
    auto_trade_symbol_cooldown_seconds: float = 300.0  # no re-abrir el mismo símbolo

    @classmethod
    def from_settings(cls, overrides: Optional[dict[str, Any]] = None) -> "ScannerConfig":
        """Construye la config desde `settings.yaml` (sección `scanner`)."""
        data: dict[str, Any] = dict(SCANNER_CFG or {})
        if overrides:
            data.update(overrides)
        return cls(**data)
