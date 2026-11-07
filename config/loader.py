# config/loader.py
"""
Carga centralizada de configuración.

- Lee ``secrets.env`` para claves sensibles (solo alertas; el monitor no usa
  API keys de trading).
- Lee ``settings.yaml`` para fases, timeframes, scanner, monitor, etc.
- Expone constantes para el resto del proyecto.
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from dotenv import load_dotenv
from loguru import logger

from bot.core.config_model import PhaseConfig


def _parse_bool(value: Any) -> bool:
    """Interpreta strings habituales de configuración como booleanos."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


# ── 1. Variables de entorno (alertas) ───────────────────
ENV_PATH = Path(__file__).parent / "secrets.env"
load_dotenv(dotenv_path=ENV_PATH)

TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
DISCORD_WEBHOOK: str = os.getenv("DISCORD_WEBHOOK", "")

# Los clientes REST públicos no requieren keys; se mantienen por compatibilidad.
API_KEY: str | None = os.getenv("BINANCE_API_KEY")
API_SECRET: str | None = os.getenv("BINANCE_API_SECRET")

# ── 2. Configuración YAML ────────────────────────────────
YAML_PATH = Path(__file__).parent / "settings.yaml"
with open(YAML_PATH, "r", encoding="utf-8") as f:
    _settings = yaml.safe_load(f)

# ── 3. Secciones ─────────────────────────────────────────
EXCHANGE: Dict[str, Any] = _settings.get("exchange", {})
TESTNET: bool = _parse_bool(EXCHANGE.get("testnet", True))

SYMBOL: str = _settings.get("symbol", "XRPUSDT")
MONITOR_ONLY: bool = _parse_bool(_settings.get("monitor_only", True))
TIMEZONE_NAME: str = _settings.get("timezone", "America/Asuncion")
FASES_ESCALAMIENTO: Dict[int, Dict[str, Any]] = _settings.get("phases", {})
TIMEFRAMES: Dict[str, Any] = _settings.get("timeframes", {})
MONITOR_CFG: Dict[str, Any] = _settings.get("monitor", {})
SCANNER_CFG: Dict[str, Any] = _settings.get("scanner", {})
RATE_LIMITER: Dict[str, Any] = _settings.get("rate_limiter", {})
MARKET_DATA: Dict[str, Any] = _settings.get("market_data", {})

FUNDING_RATE_CFG: Dict[str, Any] = MARKET_DATA.get("funding_rate", {})
FUNDING_RATE_ENABLED: bool = FUNDING_RATE_CFG.get("enabled", True)
FUNDING_RATE_EXTREME_HIGH: float = FUNDING_RATE_CFG.get("extreme_high", 0.01)
FUNDING_RATE_EXTREME_LOW: float = FUNDING_RATE_CFG.get("extreme_low", -0.01)
OPEN_INTEREST_ENABLED: bool = MARKET_DATA.get("open_interest", {}).get("enabled", False)

RL_ENABLED: bool = RATE_LIMITER.get("enabled", True)
RL_TOKENS_PER_SECOND: float = RATE_LIMITER.get("tokens_per_second", 5.0)
RL_MAX_TOKENS: float = RATE_LIMITER.get("max_tokens", 10.0)

TIMEFRAME_PRIMARY = TIMEFRAMES.get("primary", "60")
TIMEFRAME_ENTRY = TIMEFRAMES.get("entry", "15")
VELAS_LIMIT_PRIMARY = TIMEFRAMES.get("limit_primary", 30)
VELAS_LIMIT_ENTRY = TIMEFRAMES.get("limit_entry", 30)

# ── Directorios ──────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent  # raíz del proyecto
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)


# ── 4. Utilidad: fase actual según balance ──────────────
def get_phase_config(balance: float,
                     profit_factor: Optional[float] = None,
                     kelly_sizing: Any = None) -> tuple[int, Dict[str, Any]]:
    """
    Devuelve el número de fase y su configuración según el balance.

    Si el profit factor de los últimos trades es menor a 1.0, se fuerza una
    fase por debajo de la que correspondería por balance (fase defensiva).
    """
    fase_base = 0
    for fase_num, fase_config in FASES_ESCALAMIENTO.items():
        if fase_config["balance_min"] <= balance < fase_config["balance_max"]:
            fase_base = fase_num
            break
    else:
        fase_base = min(FASES_ESCALAMIENTO.keys())

    pf = profit_factor
    if pf is None and kelly_sizing is not None:
        try:
            stats = kelly_sizing.stats
            if stats.get("status") == "active":
                pf = stats.get("profit_factor")
        except Exception:
            pf = None

    if pf is not None and pf < 1.0 and fase_base > 0:
        fase_defensiva = max(fase_base - 1, 0)
        logger.warning(
            f"⚡ Fase defensiva activada: profit_factor={pf:.2f} < 1.0, "
            f"degradando de fase {fase_base} a fase {fase_defensiva}"
        )
        return fase_defensiva, FASES_ESCALAMIENTO.get(fase_defensiva, FASES_ESCALAMIENTO[fase_base])

    return fase_base, FASES_ESCALAMIENTO[fase_base]


# ── 5. Validación de configuración ──────────────────────
def validate_config(raise_on_error: bool = True) -> list[str]:
    """Valida las fases de escalamiento con Pydantic."""
    messages: list[str] = []
    for fase_num, cfg in FASES_ESCALAMIENTO.items():
        try:
            PhaseConfig(**cfg)
        except Exception as e:
            msg = f"Fase {fase_num} inválida en settings.yaml: {e}"
            if raise_on_error:
                raise ValueError(msg) from e
            messages.append(f"❌ {msg}")
            continue
    messages.append(f"✅ {len(FASES_ESCALAMIENTO)} fase(s) validada(s) con Pydantic")
    return messages


# ── Auto-validación al importar (desactivable con env var) ──
if os.getenv("SKIP_CONFIG_VALIDATION", "").lower() not in ("1", "true", "yes"):
    try:
        validate_config(raise_on_error=True)
    except ValueError:
        raise
    except Exception:
        pass
