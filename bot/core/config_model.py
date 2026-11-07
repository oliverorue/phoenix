"""Validación de configuración con Pydantic V2."""

from pydantic import BaseModel, Field, field_validator
from typing import Any


class PhaseConfig(BaseModel):
    balance_min: float = Field(ge=0)
    balance_max: float = Field(gt=0)
    leverage: int = Field(ge=1, le=5)
    risk_per_trade: float = Field(ge=0.001, le=0.1)
    max_dd_daily: float = Field(ge=0.01, le=0.5)
    max_stops_day: int = Field(ge=1, le=20)
    max_sl_pct: float = Field(ge=0.005, le=0.2)
    sl_buffer: float = Field(ge=0.0001, le=0.05)
    tp1_rr: float = Field(ge=0.5, le=5.0)
    tp2_rr: float = Field(ge=1.0, le=10.0)
    tp1_porcentaje: float = Field(ge=0.1, le=0.9)
    breakeven_trigger: float = Field(ge=0.0, le=1.0)
    rsi_min_long: int = Field(ge=0, le=50)
    rsi_max_long: int = Field(ge=20, le=70)
    rsi_min_short: int = Field(ge=30, le=70)
    rsi_max_short: int = Field(ge=50, le=100)
    sesion_inicio: int = Field(ge=0, le=23)
    sesion_fin: int = Field(ge=1, le=24)
    trailing_dist: float = Field(ge=0.0001, le=0.01)
    max_volatility_pct: float = Field(ge=0.5, le=20.0)
    lateral_adx_min: int = Field(ge=10, le=50)
    lateral_ma_distance: float = Field(ge=0.001, le=0.05)
    descripcion: str = ""

    @field_validator('balance_max')
    @classmethod
    def balance_range_valid(cls, v: float, info: Any) -> float:
        if info.data.get('balance_min', 0) >= v:
            raise ValueError('balance_max debe ser mayor que balance_min')
        return v

    @field_validator('sesion_fin')
    @classmethod
    def sesion_valid(cls, v: int, info: Any) -> int:
        if info.data.get('sesion_inicio', 0) >= v:
            raise ValueError('sesion_fin debe ser mayor que sesion_inicio')
        return v