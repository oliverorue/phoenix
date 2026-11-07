# config/loader.py
"""
Carga centralizada de configuración:
- Lee secrets.env para claves de API y variables sensibles.
- Lee settings.yaml para fases, símbolos, timeframes, etc.
- Expone constantes para que el resto del bot las importe.
"""

import os
from pathlib import Path
from typing import Dict, Any, Optional
from bot.core.config_model import PhaseConfig

import yaml
from loguru import logger
from dotenv import load_dotenv


def _parse_bool(value: Any) -> bool:
    """Interpreta strings habituales de configuración como booleanos."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def resolve_testnet(settings_value: bool, env_value: str | None) -> tuple[bool, str]:
    """Resuelve el modo testnet/mainnet.

    ``exchange.testnet`` de settings.yaml es la fuente principal. La variable
    de entorno ``TESTNET`` puede actuar como override explícito, pero si no
    coincide con settings.yaml se considera un conflicto de configuración.
    """
    if env_value is None or not str(env_value).strip():
        return bool(settings_value), "settings"
    return _parse_bool(env_value), "env"


# ── 1. Cargar variables de entorno ──────────────────────
ENV_PATH = Path(__file__).parent / "secrets.env"
load_dotenv(dotenv_path=ENV_PATH)

# ── Secretos vía Secrets Manager ─────────────────────────
# Se cargan primero desde .env (compatibilidad), luego desde
# SecretsManager (Vault / archivo cifrado) si está disponible.
# Mantenemos las constantes para que los imports existentes
# (from config.loader import API_KEY) sigan funcionando.
API_KEY: str | None = os.getenv("BINANCE_API_KEY")
API_SECRET: str | None = os.getenv("BINANCE_API_SECRET")
TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
DISCORD_WEBHOOK: str = os.getenv("DISCORD_WEBHOOK", "")

# ── 2. Cargar configuración YAML ────────────────────────
YAML_PATH = Path(__file__).parent / "settings.yaml"
with open(YAML_PATH, "r", encoding="utf-8") as f:
    _settings = yaml.safe_load(f)

# ── 3. Extraer secciones principales ────────────────────
EXCHANGE: Dict[str, Any] = _settings.get("exchange", {})

# ── 3a. Modo testnet/mainnet y confirmación de trading real ──
_SETTINGS_TESTNET: bool = _parse_bool(EXCHANGE.get("testnet", True))
TESTNET, TESTNET_SOURCE = resolve_testnet(
    _SETTINGS_TESTNET, os.getenv("TESTNET")
)
# True solo si el operador confirmó explícitamente operar en mainnet.
LIVE_TRADING_CONFIRMED: bool = _parse_bool(
    os.getenv("LIVE_TRADING_CONFIRMED", "false")
)
# Hay conflicto si el env dice una cosa y settings.yaml dice otra.
TESTNET_CONFLICT: bool = (
    TESTNET_SOURCE == "env" and TESTNET != _SETTINGS_TESTNET
)

SYMBOL: str = _settings.get("symbol", "XRPUSDT")
MONITOR_ONLY: bool = _parse_bool(_settings.get("monitor_only", True))
TIMEZONE_NAME: str = _settings.get("timezone", "America/Asuncion")
FASES_ESCALAMIENTO: Dict[int, Dict[str, Any]] = _settings.get("phases", {})
SUPPORTED_COINS: Dict[str, Dict[str, Any]] = _settings.get("supported_coins", {})
TIMEFRAMES: Dict[str, Any] = _settings.get("timeframes", {})
INDICATORS: Dict[str, Any] = _settings.get("indicators", {})
EXECUTION: Dict[str, Any] = _settings.get("execution", {})
API_RESILIENCE: Dict[str, Any] = _settings.get("api", {})
DASHBOARD: Dict[str, Any] = _settings.get("dashboard", {})

# ── 4a. Paper Trading ─────────────────────────────────────────
PAPER_TRADING_CFG: Dict[str, Any] = _settings.get("paper_trading", {})
PAPER_TRADING_ENABLED: bool = PAPER_TRADING_CFG.get("enabled", False)
PAPER_INITIAL_BALANCE: float = float(PAPER_TRADING_CFG.get("initial_balance", 10000.0))
PAPER_SLIPPAGE_PCT: float = float(PAPER_TRADING_CFG.get("slippage_pct", 0.001))
PAPER_COMMISSION_PCT: float = float(PAPER_TRADING_CFG.get("commission_pct", 0.0005))
PAPER_MAKER_COMMISSION_PCT: float = float(PAPER_TRADING_CFG.get("maker_commission_pct", 0.0001))

# ── 4a. Monitor de señales (solo lectura, sin ejecución) ──────
MONITOR_CFG: Dict[str, Any] = _settings.get("monitor", {})

# ── 4a. Portfolio / Correlación ─────────────────────────────
_PORTFOLIO_CFG: Dict[str, Any] = _settings.get("portfolio", {})
CORRELATION_GROUPS: list[Dict[str, Any]] = _PORTFOLIO_CFG.get("correlation_groups", [])

# ── 4ab. Kelly Criterion Dynamic Sizing ─────────────────────
KELLY_SIZING_CFG: Dict[str, Any] = _settings.get("kelly_sizing", {})

# ── 4ac. Portfolio Risk (VaR / Exposición Total) ──────────────
PORTFOLIO_RISK_CFG: Dict[str, Any] = _settings.get("portfolio_risk", {})

# ── 4ad. Secrets Manager ────────────────────────────────────────
SECRETS_CFG: Dict[str, Any] = _settings.get("secrets", {})
SECRETS_REQUIRED_KEYS: list[str] = SECRETS_CFG.get("required_keys", [
    "BINANCE_API_KEY", "BINANCE_API_SECRET",
    "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "DISCORD_WEBHOOK",
])
SECRETS_VAULT_CFG: Dict[str, Any] = SECRETS_CFG.get("vault", {})
SECRETS_VAULT_ENABLED: bool = SECRETS_VAULT_CFG.get("enabled", False)
SECRETS_VAULT_URL: str = SECRETS_VAULT_CFG.get("url", "")
SECRETS_VAULT_PATH: str = SECRETS_VAULT_CFG.get("path", "secret/data/trading-bot")
SECRETS_VAULT_MOUNT: str = SECRETS_VAULT_CFG.get("mount", "secret")
SECRETS_VAULT_TTL: int = SECRETS_VAULT_CFG.get("ttl", 300)
SECRETS_ENC_FILE: str = SECRETS_CFG.get("enc_file", "config/secrets.env.enc")
SECRETS_ENC_KEY_ENV: str = SECRETS_CFG.get("enc_key_env", "SECRETS_ENCRYPTION_KEY")

# ── 4ae. Graceful Shutdown ─────────────────────────────────────
SHUTDOWN_CFG: Dict[str, Any] = _settings.get("shutdown", {})
SHUTDOWN_TIMEOUT: float = float(SHUTDOWN_CFG.get("timeout", 30.0))
SHUTDOWN_CLOSE_POSITIONS: bool = SHUTDOWN_CFG.get("close_positions", True)
SHUTDOWN_NOTIFY: bool = SHUTDOWN_CFG.get("notify", True)

# ── 4af. Métricas exportables (Prometheus + JSON) ──────────────
METRICS_CFG: Dict[str, Any] = _settings.get("metrics", {})
METRICS_ENABLED: bool = METRICS_CFG.get("enabled", True)
METRICS_PORT: int = METRICS_CFG.get("port", 8000)
METRICS_JSON_PATH: str = METRICS_CFG.get("json_path", "data/metrics.json")

# ── 4ae. Tracing distribuido (OpenTelemetry) ──────────────────
TRACING_CFG: Dict[str, Any] = _settings.get("tracing", {})
# Deshabilitado por defecto: el exporter console inunda logs en producción.
TRACING_ENABLED: bool = TRACING_CFG.get("enabled", False)
TRACING_EXPORTER: str = TRACING_CFG.get("exporter", "otlp")
TRACING_OTLP_ENDPOINT: str = TRACING_CFG.get("otlp_endpoint", "")
TRACING_SAMPLING_RATE: float = float(TRACING_CFG.get("sampling_rate", 1.0))

# ── 4a. WebSocket ──────────────────────────────────────────
WEBSOCKET: Dict[str, Any] = _settings.get("websocket", {})
WS_ENABLED: bool = WEBSOCKET.get("enabled", False)
WS_KLINE_INTERVALS: list[int] = WEBSOCKET.get("kline_intervals", [15, 60])

# ── 4a1. Orderbook WebSocket ────────────────────────────────
_OB_CFG: Dict[str, Any] = WEBSOCKET.get("orderbook", {})
ORDERBOOK_ENABLED: bool = _OB_CFG.get("enabled", True)
ORDERBOOK_DEPTH: int = _OB_CFG.get("depth", 50)

# ── 4b. Rate Limiter ───────────────────────────────────────
RATE_LIMITER: Dict[str, Any] = _settings.get("rate_limiter", {})
RL_ENABLED: bool = RATE_LIMITER.get("enabled", True)
RL_TOKENS_PER_SECOND: float = RATE_LIMITER.get("tokens_per_second", 5.0)
RL_MAX_TOKENS: float = RATE_LIMITER.get("max_tokens", 10.0)

# ── 4c. Market Data (Funding Rate / Open Interest) ─────────
MARKET_DATA: Dict[str, Any] = _settings.get("market_data", {})
FUNDING_RATE_CFG: Dict[str, Any] = MARKET_DATA.get("funding_rate", {})
FUNDING_RATE_ENABLED: bool = FUNDING_RATE_CFG.get("enabled", True)
FUNDING_RATE_EXTREME_HIGH: float = FUNDING_RATE_CFG.get("extreme_high", 0.01)
FUNDING_RATE_EXTREME_LOW: float = FUNDING_RATE_CFG.get("extreme_low", -0.01)
OPEN_INTEREST_CFG: Dict[str, Any] = MARKET_DATA.get("open_interest", {})
OPEN_INTEREST_ENABLED: bool = OPEN_INTEREST_CFG.get("enabled", False)
OI_CHANGE_THRESHOLD_PCT: float = OPEN_INTEREST_CFG.get("change_threshold_pct", 15.0)

# ── 4d. Constantes de acceso directo (atajos) ────────────
TIMEFRAME_PRIMARY = TIMEFRAMES.get("primary", "60")
TIMEFRAME_ENTRY = TIMEFRAMES.get("entry", "15")
VELAS_LIMIT_PRIMARY = TIMEFRAMES.get("limit_primary", 30)
VELAS_LIMIT_ENTRY = TIMEFRAMES.get("limit_entry", 30)

MA_PERIODS = INDICATORS.get("ma_periods", [5, 10, 20])
RSI_PERIOD = INDICATORS.get("rsi_period", 14)
VOL_MA_FAST = INDICATORS.get("vol_ma_fast", 5)
VOL_MA_SLOW = INDICATORS.get("vol_ma_slow", 10)

LOOP_SLEEP_POSITION = EXECUTION.get("loop_sleep_position", 30)
LOOP_SLEEP_NO_POSITION = EXECUTION.get("loop_sleep_no_position", 90)

CIRCUIT_BREAKER_MAX_FAILURES = API_RESILIENCE.get("circuit_breaker_max_failures", 5)
CIRCUIT_BREAKER_COOLDOWN_MIN = API_RESILIENCE.get("circuit_breaker_cooldown_min", 15)
API_RETRY_MAX = API_RESILIENCE.get("retry_max", 3)
API_RETRY_BASE_DELAY = API_RESILIENCE.get("retry_base_delay", 1.0)
API_RETRY_MAX_DELAY = API_RESILIENCE.get("retry_max_delay", 30.0)
INSTRUMENT_CACHE_TTL = API_RESILIENCE.get("instrument_cache_ttl", 3600)

DASHBOARD_PORT = DASHBOARD.get("port", 8502)
DASHBOARD_HOST = DASHBOARD.get("host", "0.0.0.0")
BOT_NAME = DASHBOARD.get("bot_name", "SCALPER BOT")
BOT_VERSION = DASHBOARD.get("bot_version", "v4.0")
BOT_EMOJI = DASHBOARD.get("bot_emoji", "🤖")
# ── Directorios ────────────────────────────────
BASE_DIR = Path(__file__).parent.parent  # raíz del proyecto
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# ── 5. Función de utilidad para obtener fase actual ─────
def get_phase_config(balance: float,
                     profit_factor: Optional[float] = None,
                     kelly_sizing: Any = None) -> tuple[int, Dict[str, Any]]:
    """
    Devuelve el número de fase y su configuración según el balance.

    Fix #10: Fase defensiva — si el profit factor de los últimos trades
    es menor a 1.0, se fuerza una fase por debajo de la que correspondería
    por balance. Esto evita que el bot escale riesgo cuando la estrategia
    está perdiendo dinero.

    Args:
        balance: Balance actual en USDT.
        profit_factor: Profit factor de los últimos N trades (opcional).
                       Si es < 1.0, se fuerza fase defensiva.
        kelly_sizing: Instancia de KellySizing para obtener métricas
                      de performance adicionales (opcional).

    Returns:
        Tupla (fase_num, fase_config) según balance y performance.
    """
    # Determinar fase base por balance
    fase_base = 0
    for fase_num, fase_config in FASES_ESCALAMIENTO.items():
        if fase_config["balance_min"] <= balance < fase_config["balance_max"]:
            fase_base = fase_num
            break
    else:
        # Fallback: primera fase definida (normalmente la 0)
        fase_base = min(FASES_ESCALAMIENTO.keys())

    # Fix #10: Fase defensiva — degradar si la estrategia pierde dinero
    # Usar profit_factor pasado explícitamente o desde kelly_sizing.stats
    pf = profit_factor
    if pf is None and kelly_sizing is not None:
        try:
            stats = kelly_sizing.stats
            if stats.get("status") == "active":
                pf = stats.get("profit_factor")
        except Exception:
            pf = None

    if pf is not None and pf < 1.0 and fase_base > 0:
        # Degradar una fase: si estás en fase 2, pasas a fase 1
        fase_defensiva = max(fase_base - 1, 0)
        logger.warning(
            f"⚡ Fase defensiva activada: profit_factor={pf:.2f} < 1.0, "
            f"degradando de fase {fase_base} a fase {fase_defensiva}"
        )
        return fase_defensiva, FASES_ESCALAMIENTO.get(fase_defensiva, FASES_ESCALAMIENTO[fase_base])

    return fase_base, FASES_ESCALAMIENTO[fase_base]


# ── 6. (Opcional) Log de arranque ───────────────────────
if __name__ == "__main__":
    print(f"🧪 Test de config/loader.py")
    print(f"   Symbol: {SYMBOL}")
    print(f"   Testnet: {TESTNET}")
    print(f"   Fases cargadas: {len(FASES_ESCALAMIENTO)}")
    print(f"   Monedas soportadas: {list(SUPPORTED_COINS.keys())}")
    fase, cfg = get_phase_config(50)
    print(f"   Balance $50 → Fase {fase}: {cfg['descripcion']}")


# ── 6. Seguridad de ejecución (paper/testnet/mainnet) ─────────────

class RuntimeSafetyError(RuntimeError):
    """La configuración no es segura para el modo de ejecución solicitado."""


def _looks_like_placeholder(value: str | None) -> bool:
    """True si el secreto está vacío o sigue siendo un placeholder."""
    if value is None:
        return True
    normalized = value.strip().upper()
    if not normalized:
        return True
    return normalized.startswith(("TU_", "YOUR_", "CHANGEME", "REPLACE_"))


def validate_runtime_safety(
    paper_mode: Optional[bool] = None,
    testnet: Optional[bool] = None,
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
) -> list[str]:
    """Valida que la combinación de modo y credenciales sea segura.

    Reglas de Fase 0:
      - ``exchange.testnet`` y ``TESTNET`` deben coincidir (sin ambigüedad).
      - En mainnet real (paper=False, testnet=False) se exige
        ``LIVE_TRADING_CONFIRMED=true``.
      - Cualquier modo con órdenes reales (paper=False) exige API keys reales.

    Retorna una lista de errores. Lista vacía significa configuración válida.
    """
    paper = PAPER_TRADING_ENABLED if paper_mode is None else bool(paper_mode)
    is_testnet = TESTNET if testnet is None else bool(testnet)

    errors: list[str] = []

    if TESTNET_CONFLICT:
        errors.append(
            "Conflicto de entorno: TESTNET="
            f"{TESTNET} en variable de entorno, pero exchange.testnet="
            f"{_SETTINGS_TESTNET} en settings.yaml. "
            "Unificá ambos valores antes de arrancar."
        )

    if not paper:
        if _looks_like_placeholder(api_key if api_key is not None else API_KEY):
            errors.append("BINANCE_API_KEY no está configurada o es un placeholder.")
        if _looks_like_placeholder(api_secret if api_secret is not None else API_SECRET):
            errors.append("BINANCE_API_SECRET no está configurada o es un placeholder.")

        if not is_testnet and not LIVE_TRADING_CONFIRMED:
            errors.append(
                "Mainnet real bloqueado: paper_trading.enabled=false y "
                "exchange.testnet=false. Para habilitarlo, definí "
                "LIVE_TRADING_CONFIRMED=true de forma explícita."
            )

    return errors


def assert_runtime_safety(
    paper_mode: Optional[bool] = None,
    testnet: Optional[bool] = None,
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
) -> None:
    """Versión fail-fast de :func:`validate_runtime_safety`."""
    errors = validate_runtime_safety(
        paper_mode=paper_mode,
        testnet=testnet,
        api_key=api_key,
        api_secret=api_secret,
    )
    if errors:
        raise RuntimeSafetyError(" | ".join(errors))


# ── 7. Validación opcional (llamar explícitamente si se desea) ─────

def validate_config(raise_on_error: bool = True) -> list[str]:
    """
    Valida todas las secciones de configuración con Pydantic.
    Retorna una lista de mensajes informativos.
    Llama a esta función al inicio si quieres verificar la configuración.
    """
    from bot.core.config_model import SymbolConfig
    messages: list[str] = []

    # Validar fases
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

    # Validar portfolio
    portfolio = _settings.get("portfolio", {}).get("symbols", [])
    for sym in portfolio:
        try:
            SymbolConfig(**sym)
        except Exception as e:
            msg = f"Símbolo {sym.get('symbol', '?')} inválido: {e}"
            if raise_on_error:
                raise ValueError(msg) from e
            messages.append(f"❌ {msg}")
            continue
    if portfolio:
        messages.append(f"✅ Portfolio ({len(portfolio)} símbolo(s)) validado con Pydantic")
    else:
        messages.append("ℹ️ Sin portfolio definido en settings.yaml")

    return messages


# ── Auto‑validación al importar (se puede desactivar con env var) ──
if os.getenv("SKIP_CONFIG_VALIDATION", "").lower() not in ("1", "true", "yes"):
    try:
        validate_config(raise_on_error=True)
    except ValueError:
        raise  # Relanza errores de validación
    except Exception:
        pass  # Otros errores se silencian