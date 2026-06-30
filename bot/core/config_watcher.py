"""
ConfigWatcher — Recarga en caliente de config/settings.yaml.

Monitorea cambios en el archivo YAML y dispara recarga automática
sin necesidad de reiniciar el bot. Las secciones que se actualizan
en caliente incluyen fases, indicadores, ejecución, paper trading, etc.

Uso:
    from bot.core.config_watcher import config_watcher

    # Iniciar el watcher como tarea de fondo
    asyncio.create_task(config_watcher.watch())

    # Obtener valores siempre actualizados
    loop_sleep = config_watcher.get("execution", "loop_sleep_position", 30)
"""

import os
from pathlib import Path
from typing import Any
from loguru import logger

# ── Ruta al YAML de configuración ───────────────────────────────
_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "settings.yaml"


class ConfigWatcher:
    """Monitorea ``settings.yaml`` y recarga configuración en caliente.

    Attributes:
        check_interval: Segundos entre cada verificación de mtime.
        _settings: Dict con la configuración actual (recargada).
        _mtime: Último mtime conocido del archivo.
        _callbacks: Lista de (sección, callable) a invocar al recargar.
    """

    def __init__(self, check_interval: float = 5.0) -> None:
        self.check_interval = check_interval
        self._settings: dict[str, Any] = {}
        self._mtime: float = 0.0
        self._callbacks: list[tuple[str, Any]] = []
        self._load()

    # ── Carga inicial ────────────────────────────────────────────

    def _load(self) -> None:
        """Lee el YAML y actualiza ``_settings`` y ``_mtime``."""
        try:
            import yaml
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                self._settings = yaml.safe_load(f) or {}
            self._mtime = os.path.getmtime(_CONFIG_PATH)
            logger.debug(f"📄 ConfigWatcher: cargó {_CONFIG_PATH.name}")
        except Exception as e:
            logger.error(f"❌ ConfigWatcher: error al cargar {_CONFIG_PATH.name}: {e}")
            self._settings = {}

    # ── Detección de cambios ─────────────────────────────────────

    def _has_changed(self) -> bool:
        """True si el archivo fue modificado desde la última carga."""
        try:
            return os.path.getmtime(_CONFIG_PATH) > self._mtime
        except OSError:
            return False

    # ── Recarga ──────────────────────────────────────────────────

    def reload(self) -> bool:
        """Recarga el YAML si cambió. Retorna True si hubo cambios."""
        if not self._has_changed():
            return False

        old_settings = self._settings
        self._load()

        if not self._settings:
            logger.warning("⚠️ ConfigWatcher: YAML vacío tras recarga, revirtiendo...")
            self._settings = old_settings
            return False

        logger.info(f"🔄 ConfigWatcher: {_CONFIG_PATH.name} recargado")

        # Notificar callbacks
        for section, cb in self._callbacks:
            new_val = self._settings.get(section, {})
            old_val = old_settings.get(section, {})
            if new_val != old_val:
                try:
                    cb(new_val, old_val)
                except Exception as e:
                    logger.error(f"❌ ConfigWatcher: error en callback de '{section}': {e}")

        # Sincronizar con config/loader.py (variables de módulo)
        _sync_module_vars(self._settings)

        return True

    # ── Watch loop asíncrono ─────────────────────────────────────

    async def watch(self) -> None:
        """Loop infinito que verifica cambios cada ``check_interval`` segundos."""
        logger.info(f"👀 ConfigWatcher: vigilando {_CONFIG_PATH.name} cada {self.check_interval}s")
        while True:
            try:
                self.reload()
            except Exception as e:
                logger.error(f"❌ ConfigWatcher: error en watch loop: {e}")
            await asyncio.sleep(self.check_interval)

    # ── Acceso a configuración ───────────────────────────────────

    def get(self, *keys: str, default: Any = None) -> Any:
        """Acceso seguro a valores anidados del YAML.

        Ejemplos::

            config_watcher.get("execution", "loop_sleep_position", default=30)
            config_watcher.get("phases", "0", "leverage", default=3)
        """
        if not keys:
            return self._settings
        val: Any = self._settings
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k)
                if val is None:
                    return default
            else:
                return default
        return val if val is not None else default

    # ── Callbacks ────────────────────────────────────────────────

    def on_change(self, section: str) -> Any:
        """Decorador para registrar callback cuando una sección cambia.

        Uso::

            @config_watcher.on_change("phases")
            def on_phases_change(new_val, old_val):
                logger.info(f"Fases actualizadas: {new_val}")
        """
        def decorator(func: Any) -> Any:
            self._callbacks.append((section, func))
            return func
        return decorator

    def register_callback(self, section: str, callback: Any) -> None:
        """Registra un callback para una sección."""
        self._callbacks.append((section, callback))


# ── Sincronización con config/loader.py ─────────────────────────

def _sync_module_vars(settings: dict[str, Any]) -> None:
    """Actualiza las variables de módulo en ``config.loader``.

    Esto permite que los imports existentes (``from config.loader import X``)
    sigan funcionando con valores actualizados sin cambiar el código.
    """
    try:
        import config.loader as cl

        # Secciones que se pueden recargar en caliente
        cl._settings = settings
        cl.EXCHANGE = settings.get("exchange", {})
        cl.SYMBOL = settings.get("symbol", "XRPUSDT")
        cl.FASES_ESCALAMIENTO = settings.get("phases", {})
        cl.SUPPORTED_COINS = settings.get("supported_coins", {})
        cl.TIMEFRAMES = settings.get("timeframes", {})
        cl.INDICATORS = settings.get("indicators", {})
        cl.EXECUTION = settings.get("execution", {})
        cl.API_RESILIENCE = settings.get("api", {})
        cl.DASHBOARD = settings.get("dashboard", {})

        # Paper trading
        pt = settings.get("paper_trading", {})
        cl.PAPER_TRADING_CFG = pt
        cl.PAPER_TRADING_ENABLED = pt.get("enabled", False)
        cl.PAPER_INITIAL_BALANCE = float(pt.get("initial_balance", 10000.0))
        cl.PAPER_SLIPPAGE_PCT = float(pt.get("slippage_pct", 0.001))
        cl.PAPER_COMMISSION_PCT = float(pt.get("commission_pct", 0.0005))

        # Portfolio / Kelly / Risk
        portfolio = settings.get("portfolio", {})
        cl._PORTFOLIO_CFG = portfolio
        cl.CORRELATION_GROUPS = portfolio.get("correlation_groups", [])
        cl.KELLY_SIZING_CFG = settings.get("kelly_sizing", {})
        cl.PORTFOLIO_RISK_CFG = settings.get("portfolio_risk", {})

        # Métricas
        metrics = settings.get("metrics", {})
        cl.METRICS_CFG = metrics
        cl.METRICS_ENABLED = metrics.get("enabled", True)
        cl.METRICS_PORT = metrics.get("port", 8000)
        cl.METRICS_JSON_PATH = metrics.get("json_path", "data/metrics.json")

        # WebSocket
        ws = settings.get("websocket", {})
        cl.WEBSOCKET = ws
        cl.WS_ENABLED = ws.get("enabled", False)
        cl.WS_KLINE_INTERVALS = ws.get("kline_intervals", [15, 60])

        # Rate limiter
        rl = settings.get("rate_limiter", {})
        cl.RATE_LIMITER = rl
        cl.RL_ENABLED = rl.get("enabled", True)
        cl.RL_TOKENS_PER_SECOND = rl.get("tokens_per_second", 5.0)
        cl.RL_MAX_TOKENS = rl.get("max_tokens", 10.0)

        # Market data
        md = settings.get("market_data", {})
        cl.MARKET_DATA = md
        cl.FUNDING_RATE_CFG = md.get("funding_rate", {})
        cl.FUNDING_RATE_ENABLED = cl.FUNDING_RATE_CFG.get("enabled", True)
        cl.FUNDING_RATE_EXTREME_HIGH = cl.FUNDING_RATE_CFG.get("extreme_high", 0.01)
        cl.FUNDING_RATE_EXTREME_LOW = cl.FUNDING_RATE_CFG.get("extreme_low", -0.01)
        cl.OPEN_INTEREST_CFG = md.get("open_interest", {})
        cl.OPEN_INTEREST_ENABLED = cl.OPEN_INTEREST_CFG.get("enabled", False)
        cl.OI_CHANGE_THRESHOLD_PCT = cl.OPEN_INTEREST_CFG.get("change_threshold_pct", 15.0)

        # Timeframes shortcuts
        cl.TIMEFRAME_PRIMARY = cl.TIMEFRAMES.get("primary", "60")
        cl.TIMEFRAME_ENTRY = cl.TIMEFRAMES.get("entry", "15")
        cl.VELAS_LIMIT_PRIMARY = cl.TIMEFRAMES.get("limit_primary", 30)
        cl.VELAS_LIMIT_ENTRY = cl.TIMEFRAMES.get("limit_entry", 30)

        # Indicadores shortcuts
        cl.MA_PERIODS = cl.INDICATORS.get("ma_periods", [5, 10, 20])
        cl.RSI_PERIOD = cl.INDICATORS.get("rsi_period", 14)
        cl.VOL_MA_FAST = cl.INDICATORS.get("vol_ma_fast", 5)
        cl.VOL_MA_SLOW = cl.INDICATORS.get("vol_ma_slow", 10)

        # Ejecución shortcuts
        cl.LOOP_SLEEP_POSITION = cl.EXECUTION.get("loop_sleep_position", 30)
        cl.LOOP_SLEEP_NO_POSITION = cl.EXECUTION.get("loop_sleep_no_position", 90)

        # API resilience shortcuts
        cl.CIRCUIT_BREAKER_MAX_FAILURES = cl.API_RESILIENCE.get("circuit_breaker_max_failures", 5)
        cl.CIRCUIT_BREAKER_COOLDOWN_MIN = cl.API_RESILIENCE.get("circuit_breaker_cooldown_min", 15)
        cl.API_RETRY_MAX = cl.API_RESILIENCE.get("retry_max", 3)
        cl.API_RETRY_BASE_DELAY = cl.API_RESILIENCE.get("retry_base_delay", 1.0)
        cl.API_RETRY_MAX_DELAY = cl.API_RESILIENCE.get("retry_max_delay", 30.0)
        cl.INSTRUMENT_CACHE_TTL = cl.API_RESILIENCE.get("instrument_cache_ttl", 3600)

        # Secrets Manager
        sec = settings.get("secrets", {})
        cl.SECRETS_CFG = sec
        cl.SECRETS_REQUIRED_KEYS = sec.get("required_keys", [
            "BYBIT_API_KEY", "BYBIT_API_SECRET",
            "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "DISCORD_WEBHOOK",
        ])
        sv = sec.get("vault", {})
        cl.SECRETS_VAULT_CFG = sv
        cl.SECRETS_VAULT_ENABLED = sv.get("enabled", False)
        cl.SECRETS_VAULT_URL = sv.get("url", "")
        cl.SECRETS_VAULT_PATH = sv.get("path", "secret/data/trading-bot")
        cl.SECRETS_VAULT_MOUNT = sv.get("mount", "secret")
        cl.SECRETS_VAULT_TTL = sv.get("ttl", 300)
        cl.SECRETS_ENC_FILE = sec.get("enc_file", "config/secrets.env.enc")
        cl.SECRETS_ENC_KEY_ENV = sec.get("enc_key_env", "SECRETS_ENCRYPTION_KEY")

        # Shutdown
        sd = settings.get("shutdown", {})
        cl.SHUTDOWN_CFG = sd
        cl.SHUTDOWN_TIMEOUT = float(sd.get("timeout", 30.0))
        cl.SHUTDOWN_CLOSE_POSITIONS = sd.get("close_positions", True)
        cl.SHUTDOWN_NOTIFY = sd.get("notify", True)

        # Dashboard
        cl.DASHBOARD_PORT = cl.DASHBOARD.get("port", 8502)
        cl.DASHBOARD_HOST = cl.DASHBOARD.get("host", "0.0.0.0")
        cl.BOT_NAME = cl.DASHBOARD.get("bot_name", "SCALPER BOT")
        cl.BOT_VERSION = cl.DASHBOARD.get("bot_version", "v4.0")
        cl.BOT_EMOJI = cl.DASHBOARD.get("bot_emoji", "🤖")

        logger.debug("🔄 ConfigWatcher: variables de config/loader sincronizadas")
    except Exception as e:
        logger.error(f"❌ ConfigWatcher: error sincronizando config/loader: {e}")


# ── Singleton global ────────────────────────────────────────────
config_watcher = ConfigWatcher()

# Evitar import circular en asyncio
import asyncio  # noqa: E402
