"""
Modelos de dominio del monitor global (pydantic v2).

Estos objetos viajan entre el scanner (async), el motor de anomalías y la UI.
Mantienen type hints estables y validación en los bordes fríos del sistema.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Direction = Literal["LONG", "SHORT", "NEUTRAL"]


class PairMeta(BaseModel):
    """Metadatos de un par perpetuo USDT-M elegible para el scanner."""

    symbol: str
    base_asset: str = ""
    quote_asset: str = "USDT"
    status: str = "TRADING"
    contract_type: str = "PERPETUAL"
    last_price: float = 0.0
    price_change_pct: float = 0.0
    quote_volume_24h: float = 0.0


class Signal(BaseModel):
    """Tarjeta de anomalía que se muestra en la UI.

    ``direction`` es la hipótesis direccional (LONG/SHORT), ``reasons`` los
    motivos legibles y ``confidence`` un score normalizado [0, 1].
    """

    symbol: str
    direction: Direction = "NEUTRAL"
    reasons: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    price: float = 0.0
    ts: float = 0.0
    metrics: dict[str, float] = Field(default_factory=dict)


class Sizing(BaseModel):
    """Recomendación de tamaño de posición (Kelly) para la UI."""

    symbol: str
    risk_usdt: float = 0.0
    risk_pct: float = 0.0
    kelly_multiplier: float = 1.0
    note: str = ""


class ScannerStats(BaseModel):
    """Instantánea de salud del scanner para el dashboard."""

    universe: int = 0
    tracked: int = 0
    connections: int = 0
    events_processed: int = 0
    signals_emitted: int = 0
    dropped: int = 0
    reconnects: int = 0
    uptime_seconds: float = 0.0
    last_error: Optional[str] = None
    diagnostics: dict[str, int] = Field(default_factory=dict)
    stream_counts: dict[str, int] = Field(default_factory=dict)
