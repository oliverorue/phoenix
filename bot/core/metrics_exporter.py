"""
Exportador de métricas para Prometheus — exposición estructurada del estado del bot.

Proporciona:
- Endpoint HTTP `/metrics` en formato Prometheus (vía `prometheus_client`)
- JSON estructurado opcional en archivo para consumo externo
- Labels por símbolo para métricas desagregadas

Uso:
    exporter = MetricsExporter(port=8000)
    exporter.set_bot_ref(bot_instance)  # referencia al TradingBot
    exporter.start()                     # inicia servidor HTTP en thread
    # En cada ciclo:
    exporter.update()
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger

try:
    from prometheus_client import start_http_server, generate_latest, REGISTRY
    _HAS_PROMETHEUS = True
except ImportError:
    _HAS_PROMETHEUS = False
    # Stubs para que no explote si no está instalado
    start_http_server = None  # type: ignore
    generate_latest = None  # type: ignore

    class _RegistryStub:
        def get_sample_value(self, *a: Any, **kw: Any) -> Optional[float]: return None
    REGISTRY = _RegistryStub()  # type: ignore


# Ruta para el JSON estructurado (misma convención que bot_state.json)
METRICS_JSON_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "metrics.json"


class MetricsExporter:
    """
    Colecta métricas de todos los componentes del bot y las exporta
    en formato Prometheus (HTTP /metrics) y JSON estructurado.

    Attributes:
        port: Puerto para el servidor HTTP de Prometheus.
        update_interval: Segundos entre actualizaciones de métricas (0 = cada update()).
        bot_ref: Referencia débil al TradingBot para leer estado.
        metrics_path: Ruta para el archivo JSON de salida.
    """

    def __init__(
        self,
        port: int = 8000,
        update_interval: float = 0.0,
        metrics_path: str | Path = METRICS_JSON_PATH,
        registry: Any = None,
    ) -> None:
        self.port = port
        self.update_interval = update_interval
        self.metrics_path = Path(metrics_path)
        self.bot_ref: Any = None

        self._http_started = False
        self._lock = threading.Lock()
        self._last_update: float = 0.0

        # ── Registry personalizado (para tests) o global ────────
        if _HAS_PROMETHEUS:
            self._registry = registry if registry is not None else REGISTRY
            self._define_metrics()
        else:
            self._registry = None
            logger.warning("prometheus_client no instalado — métricas Prometheus deshabilitadas")

    # ── Definición de métricas ─────────────────────────────────

    def _define_metrics(self) -> None:
        """Registra todos los gauges, counters e histograms en el registry."""
        if not _HAS_PROMETHEUS:
            return
        from prometheus_client import Gauge, Counter

        reg: Any = self._registry

        # ── Balance y exposición ──
        self._g_balance = Gauge(
            "bot_balance_usdt", "Balance disponible en USDT", ["symbol"],
            registry=reg,
        )
        self._g_total_exposure = Gauge(
            "bot_total_exposure_usdt", "Exposición total del portfolio (mark-to-market)",
            registry=reg,
        )
        self._g_net_exposure = Gauge(
            "bot_net_exposure_usdt", "Exposición neta (LONG +, SHORT -)",
            registry=reg,
        )
        self._g_num_positions = Gauge(
            "bot_num_positions", "Número de posiciones abiertas",
            registry=reg,
        )
        self._g_effective_leverage = Gauge(
            "bot_effective_leverage", "Apalancamiento efectivo (exposure / balance)",
            registry=reg,
        )

        # ── VaR ──
        self._g_var_95 = Gauge("bot_var_95_usdt", "Value at Risk 95% (USDT)", registry=reg)
        self._g_var_99 = Gauge("bot_var_99_usdt", "Value at Risk 99% (USDT)", registry=reg)
        self._g_var_sigma = Gauge("bot_var_sigma_pct", "Desviación estándar de retornos (%)", registry=reg)

        # ── Kelly Sizing ──
        self._g_kelly_multiplier = Gauge(
            "bot_kelly_multiplier", "Multiplicador Kelly actual", ["symbol"], registry=reg,
        )
        self._g_kelly_streak = Gauge(
            "bot_kelly_streak", "Racha actual de wins/losses", ["symbol"], registry=reg,
        )
        self._g_kelly_win_rate = Gauge(
            "bot_kelly_win_rate", "Win rate (ventana Kelly)", ["symbol"], registry=reg,
        )
        self._g_kelly_total_trades = Gauge(
            "bot_kelly_total_trades", "Total de trades registrados en Kelly", ["symbol"], registry=reg,
        )

        # ── Riesgo / Drawdown ──
        self._g_drawdown_pct = Gauge("bot_drawdown_pct", "Drawdown actual (%)", registry=reg)
        self._g_stops_dia = Gauge("bot_stops_dia", "Stops realizados hoy", registry=reg)
        self._g_max_stops_dia = Gauge("bot_max_stops_dia", "Máximo de stops permitidos por día", registry=reg)
        self._g_session_active = Gauge("bot_session_active", "1 si la sesión de trading está activa", registry=reg)

        # ── Sistema ──
        self._g_uptime_seconds = Gauge("bot_uptime_seconds", "Segundos desde el inicio del bot", registry=reg)
        self._g_active_symbols = Gauge("bot_active_symbols", "Cantidad de símbolos activos", registry=reg)
        self._g_can_open = Gauge("bot_can_open", "1 si se puede abrir nueva posición (límites de exposición)", registry=reg)

        # ── Posiciones individuales ──
        self._g_position_notional = Gauge(
            "bot_position_notional_usdt", "Nocional actual de la posición", ["symbol", "side"], registry=reg,
        )
        self._g_position_unrealized_pnl = Gauge(
            "bot_position_unrealized_pnl_usdt", "PnL no realizado", ["symbol"], registry=reg,
        )

        # ── Contadores —─
        self._c_trades_total = Counter("bot_trades_total", "Total de trades cerrados", registry=reg)
        self._c_wins_total = Counter("bot_wins_total", "Total de trades ganadores", registry=reg)
        self._c_losses_total = Counter("bot_losses_total", "Total de trades perdedores", registry=reg)

        # ── WebSocket ──
        self._g_ws_connected = Gauge(
            "bot_ws_connected", "1 si el WebSocket está conectado", ["symbol"], registry=reg,
        )

        # ── Latencia API ──
        self._g_api_latency_p95 = Gauge(
            "bot_api_latency_p95_seconds", "Latencia p95 de la API", ["endpoint"], registry=reg,
        )

    # ── Vinculación con el bot ────────────────────────────────

    def set_bot_ref(self, bot: Any) -> None:
        """Guarda referencia al TradingBot para extraer estado."""
        self.bot_ref = bot

    # ── Actualización ─────────────────────────────────────────

    def update(self) -> None:
        """
        Lee el estado actual del bot y actualiza todas las métricas.
        Llámese en cada ciclo del bot (después de _update_dashboard_state).
        """
        if not _HAS_PROMETHEUS:
            self._write_json_fallback()
            return

        bot = self.bot_ref
        if bot is None:
            return

        try:
            with self._lock:
                self._update_from_bot(bot)
                self._update_portfolio_risk(bot)
                self._update_kelly(bot)
                self._update_positions(bot)
                self._update_ws(bot)
                self._update_latency(bot)
        except Exception as e:
            logger.error(f"Error actualizando métricas Prometheus: {e}")

        # JSON estructurado siempre disponible
        self._write_json_fallback()

    def _update_from_bot(self, bot: Any) -> None:
        """Métricas base desde el bot."""
        balance = getattr(bot, "balance", 0.0) or 0.0
        self._g_balance.labels(symbol="*").set(balance)

        uptime = 0.0
        if hasattr(bot, "start_time"):
            uptime = (datetime.now(timezone.utc) - bot.start_time).total_seconds()
        self._g_uptime_seconds.set(uptime)

        active = len(getattr(bot, "runners", []) or [])
        self._g_active_symbols.set(active)

        # Sesión activa cacheada por RiskManager.is_trading_session().
        session_active = 1.0
        try:
            primary = bot.runners[0] if bot.runners else None
            if primary and hasattr(primary, "risk_mgr"):
                cached = getattr(primary.risk_mgr, "_session_active", True)
                session_active = 1.0 if cached else 0.0
        except Exception:
            pass
        self._g_session_active.set(session_active)

        # Drawdown y stops
        try:
            primary = bot.runners[0] if bot.runners else None
            if primary and hasattr(primary, "risk_mgr"):
                rm = primary.risk_mgr
                self._g_stops_dia.set(getattr(rm, "stops_dia", 0))
                fase = getattr(primary, "fase_cfg", {}) or {}
                self._g_max_stops_dia.set(fase.get("max_stops_day", 4))
                dd = 0.0
                if hasattr(rm, "get_current_drawdown_pct"):
                    try:
                        import asyncio
                        loop = asyncio.get_running_loop()
                        if loop.is_running():
                            dd = 0.0  # sync context can't await
                        else:
                            coro = rm.get_current_drawdown_pct()
                            import asyncio
                            dd = asyncio.run(coro)
                    except Exception:
                        dd = 0.0
                self._g_drawdown_pct.set(dd)
        except Exception:
            pass

    def _update_portfolio_risk(self, bot: Any) -> None:
        """Métricas de PortfolioRisk."""
        pr = getattr(bot, "portfolio_risk", None)
        if pr is None:
            return
        try:
            self._g_total_exposure.set(pr.total_exposure)
            self._g_net_exposure.set(pr.net_exposure)
            self._g_num_positions.set(pr.num_positions)
            self._g_effective_leverage.set(pr.effective_leverage)
            self._g_can_open.set(1.0 if pr.can_open() else 0.0)

            var_data = pr.parametric_var()
            if "error" not in var_data:
                self._g_var_95.set(var_data.get("var_95", 0))
                self._g_var_99.set(var_data.get("var_99", 0))
                self._g_var_sigma.set(var_data.get("sigma_pct", 0))
        except Exception:
            logger.exception("Error actualizando métricas de portfolio_risk")

    def _update_kelly(self, bot: Any) -> None:
        """Métricas de KellySizing (por símbolo)."""
        ks = getattr(bot, "kelly_sizing", None)
        if ks is None:
            return
        try:
            stats = ks.stats
            for sym_stats in stats.get("symbols", [stats]):  # si hay multi-symbol o single
                sym = sym_stats.get("symbol", "*")
                self._g_kelly_multiplier.labels(symbol=sym).set(
                    sym_stats.get("multiplier", 1.0)
                )
                self._g_kelly_streak.labels(symbol=sym).set(
                    sym_stats.get("streak", 0)
                )
                self._g_kelly_win_rate.labels(symbol=sym).set(
                    sym_stats.get("win_rate", 0.0)
                )
                self._g_kelly_total_trades.labels(symbol=sym).set(
                    sym_stats.get("total_trades", 0)
                )
        except Exception:
            logger.exception("Error actualizando métricas de Kelly")

    def _update_positions(self, bot: Any) -> None:
        """Métricas por posición individual."""
        pr = getattr(bot, "portfolio_risk", None)
        if pr is None:
            return
        try:
            positions = pr.active_positions
            for p in positions:
                sym = p.get("symbol", "?")
                side = p.get("side", "?")
                self._g_position_notional.labels(symbol=sym, side=side).set(
                    p.get("current_notional", 0)
                )
                self._g_position_unrealized_pnl.labels(symbol=sym).set(
                    p.get("unrealized_pnl", 0)
                )
        except Exception:
            logger.exception("Error actualizando métricas de posiciones")

    def _update_ws(self, bot: Any) -> None:
        """Métricas de WebSocket."""
        ws_feeds = getattr(bot, "ws_feeds", {}) or {}
        for sym, ws in ws_feeds.items():
            connected = 1.0 if getattr(ws, "connected", False) else 0.0
            self._g_ws_connected.labels(symbol=sym).set(connected)

    def _update_latency(self, bot: Any) -> None:
        """Métricas de latencia de API desde DataFeed."""
        try:
            primary = bot.runners[0] if bot.runners else None
            if primary and hasattr(primary, "data_feed"):
                df = primary.data_feed
                if hasattr(df, "get_latency_stats"):
                    stats = df.get_latency_stats()
                    if isinstance(stats, dict):
                        for endpoint, data in stats.items():
                            p95 = data.get("p95", 0) if isinstance(data, dict) else 0
                            self._g_api_latency_p95.labels(endpoint=endpoint).set(p95)
                    elif isinstance(stats, list):
                        for item in stats:
                            if isinstance(item, dict):
                                ep = item.get("endpoint", "unknown")
                                p95 = item.get("p95", 0)
                                self._g_api_latency_p95.labels(endpoint=ep).set(p95)
        except Exception:
            pass

    # ── JSON estructurado ────────────────────────────────────

    def _write_json_fallback(self) -> None:
        """Escribe métricas en JSON estructurado para consumo externo."""
        bot = self.bot_ref
        if bot is None:
            return
        try:
            data = self._build_json(bot)
            self.metrics_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.metrics_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Error escribiendo metrics.json: {e}")

    def _build_json(self, bot: Any) -> dict[str, Any]:
        """Construye el dict estructurado para JSON."""
        pr = getattr(bot, "portfolio_risk", None)
        ks = getattr(bot, "kelly_sizing", None)
        runners = getattr(bot, "runners", []) or []
        ws_feeds = getattr(bot, "ws_feeds", {}) or {}

        # VaR
        var_data: dict[str, Any] = {"error": "No disponible"}
        if pr is not None:
            try:
                var_data = pr.parametric_var()
            except Exception:
                pass

        # Kelly stats
        kelly_stats: dict[str, Any] = {}
        if ks is not None:
            try:
                kelly_stats = ks.stats
            except Exception:
                pass

        # Posiciones detalladas
        positions: list[dict[str, Any]] = []
        if pr is not None:
            try:
                positions = pr.active_positions
            except Exception:
                pass

        # WebSocket status
        ws_status: dict[str, bool] = {}
        for sym, ws in ws_feeds.items():
            ws_status[sym] = bool(getattr(ws, "connected", False))

        # Balance por símbolo
        symbol_balances: dict[str, float] = {}
        for r in runners:
            symbol_balances[r.symbol] = getattr(r, "balance", 0.0)

        # Stops y drawdown
        stops_dia = 0
        drawdown_pct = 0.0
        if runners:
            try:
                rm = runners[0].risk_mgr
                stops_dia = getattr(rm, "stops_dia", 0)
            except Exception:
                pass

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "balance": getattr(bot, "balance", 0.0),
            "active_symbols": len(runners),
            "symbol_balances": symbol_balances,
            "portfolio": {
                "total_exposure": round(pr.total_exposure, 2) if pr else 0.0,
                "net_exposure": round(pr.net_exposure, 2) if pr else 0.0,
                "num_positions": pr.num_positions if pr else 0,
                "effective_leverage": pr.effective_leverage if pr else 0.0,
                "can_open": pr.can_open() if pr else True,
            } if pr else {},
            "var": var_data,
            "kelly_sizing": kelly_stats,
            "positions": positions,
            "risk": {
                "stops_dia": stops_dia,
                "drawdown_pct": round(drawdown_pct, 2),
            },
            "websocket": ws_status,
            "uptime_seconds": round(
                (datetime.now(timezone.utc) - bot.start_time).total_seconds(), 2
            ) if hasattr(bot, "start_time") else 0.0,
        }

    # ── Servidor HTTP ────────────────────────────────────────

    def start(self) -> None:
        """
        Inicia el servidor HTTP de Prometheus en un thread daemon.
        El endpoint `/metrics` queda disponible en `http://0.0.0.0:{port}`.
        """
        if not _HAS_PROMETHEUS:
            logger.warning("prometheus_client no disponible — no se inicia servidor HTTP")
            return
        if self._http_started:
            return
        try:
            start_http_server(self.port, registry=self._registry)
            self._http_started = True
            logger.info(f"📊 Métricas Prometheus en http://0.0.0.0:{self.port}/metrics")
        except Exception as e:
            logger.error(f"Error iniciando servidor Prometheus en puerto {self.port}: {e}")

    def stop(self) -> None:
        """Marca el servidor como detenido (el thread daemon se cierra solo)."""
        self._http_started = False


# ── Función helper para obtener métricas como string ──────────

def generate_metrics_text(registry: Any = None) -> str:
    """
    Genera el texto en formato Prometheus de las métricas actuales.
    Útil si se quiere servir desde otro endpoint HTTP.

    Args:
        registry: Registry de prometheus_client (por defecto el global REGISTRY).
    """
    if not _HAS_PROMETHEUS:
        return "# prometheus_client no disponible"
    reg = registry if registry is not None else REGISTRY
    return generate_latest(reg).decode("utf-8")
