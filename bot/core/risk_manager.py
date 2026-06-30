"""
Gestión de Riesgo y Money Management.

Cubre:
- DD diario, semanal y mensual (Fix #1)
- Circuit breaker por pérdidas consecutivas (Fix #3)
- validate_rr con descuento de comisiones (Fix #4)
- Modo degradado ante DD severo (Fix #5)
- Reconciliación de balance real vs esperado (Fix #6)
- Kelly condicional al score de señal (Fix #9)
"""


from typing import Any, Optional
from datetime import date
import math
from config.loader import get_phase_config
from config.loader import TIMEZONE_NAME
from datetime import datetime
import pytz
from loguru import logger
from bot.core.exceptions import RiskException

from bot.core.data_feed import decimal_places_for_qty_step

# Valores por defecto (se sobreescriben con la fase)
DEFAULT_RISK_PER_TRADE = 0.02   # 2%
DEFAULT_LEVERAGE = 1
DEFAULT_MAX_SL_PCT = 0.05       # 5% máximo de SL

# TTL para cache de fase_config (segundos)
_CACHE_TTL_S = 30

# Comisión taker por defecto (0.055% * 2 = 0.11% entrada + salida)
_DEFAULT_COMMISSION_PCT = 0.0011

# Umbral de discrepancia de balance para alertar (5%)
_BALANCE_DISCREPANCY_THRESHOLD = 0.05


class RiskManager:
    """
    Calcula tamaños de posición y valida límites de riesgo según la fase actual.

    Fixes implementados:
        v5.1: DD semanal/mensual, circuit breaker, RR neto, modo degradado,
              reconciliación de balance, score-aware sizing.
    """

    def __init__(self, data_feed: Any, override_cfg: Optional[dict[str, Any]] = None,
                 kelly_sizing: Any = None) -> None:
        """
        Args:
            data_feed: Instancia de DataFeed para consultar balance e info del instrumento.
            override_cfg: Dict opcional con parámetros para sobreescribir la config de fase.
            kelly_sizing: Instancia opcional de KellySizing para ajuste dinámico de riesgo.
        """
        self.data = data_feed
        self.capital_inicio_dia: Optional[float] = None
        # Fix #1: capital de referencia semanal y mensual
        self.capital_inicio_semana: Optional[float] = None
        self.capital_inicio_mes: Optional[float] = None
        self._semana_actual: Optional[int] = None   # isocalendar()[1]
        self._mes_actual: Optional[int] = None       # 1-12
        self.stops_dia = 0
        # Fix #3: circuito de pérdidas consecutivas
        self._consecutive_losses: int = 0
        self._loss_pause_until: float = 0.0
        self._cached_instrument_info: Optional[dict[str, Any]] = None
        self._override_cfg: dict[str, Any] = override_cfg or {}
        self.fecha_dia: Optional[date] = None
        self._notifier: Any = None
        self._peak_balance_daily: Optional[float] = None
        self._peak_balance_session: Optional[float] = None
        self._kelly_sizing: Any = kelly_sizing

        # Cache de fase_config con TTL para evitar múltiples llamadas async
        # dentro del mismo ciclo de trading
        self._cached_fase_num: Optional[int] = None
        self._cached_fase_cfg: Optional[dict[str, Any]] = None
        self._cached_fase_ts: float = 0.0

    def set_balance(self, balance: float) -> None:
        """Permite fijar un balance manual (para backtesting)."""
        self._backtest_balance = balance
        self.capital_inicio_dia = None
        self.capital_inicio_semana = None
        self.capital_inicio_mes = None
        self._semana_actual = None
        self._mes_actual = None
        self.stops_dia = 0
        self._consecutive_losses = 0
        self._loss_pause_until = 0.0
        self.fecha_dia = None
        self._notifier = None
        self._peak_balance_daily = None
        self._peak_balance_session = None

    async def get_phase_config(self) -> tuple[int, dict[str, Any]]:
        """Obtiene (fase_num, config) para el balance actual (async).
        Aplica override_cfg si existe (usado por backtester/optimizer).
        
        Incluye cache con TTL para evitar llamadas async redundantes
        dentro del mismo ciclo de trading (~15s).
        """
        import time
        now = time.monotonic()

        # Usar cache si está fresco
        if (self._cached_fase_cfg is not None
                and self._cached_fase_num is not None
                and now - self._cached_fase_ts < _CACHE_TTL_S):
            return self._cached_fase_num, self._cached_fase_cfg

        if hasattr(self, "_backtest_balance") and self._backtest_balance is not None:
            balance = self._backtest_balance
        else:
            balance = await self.data.get_balance()
        fase_num, cfg = get_phase_config(balance)
        if self._override_cfg:
            cfg = {**cfg, **self._override_cfg}

        # Actualizar cache
        self._cached_fase_num = fase_num
        self._cached_fase_cfg = cfg
        self._cached_fase_ts = now

        return fase_num, cfg

    async def calculate_position_size(self, entry_price: float, sl: float,
                                      score: float = 0.0) -> tuple[float, float, str]:
        """
        Calcula la cantidad de contratos basada en el riesgo (async).
        Aplica Kelly Criterion si está configurado.
        Fix #9: Escala el riesgo según el score de la señal.
        Fix #5: Aplica modo degradado según DD actual.
        Retorna (qty, risk_usdt, mensaje).
        """
        _, fase_cfg = await self.get_phase_config()
        if hasattr(self, "_backtest_balance") and self._backtest_balance is not None:
            balance = self._backtest_balance
        else:
            balance = await self.data.get_balance()
        risk_per_trade = fase_cfg.get("risk_per_trade", DEFAULT_RISK_PER_TRADE)

        # ── Kelly Criterion: ajuste dinámico del riesgo ──
        if self._kelly_sizing is not None:
            kelly_mult = self._kelly_sizing.get_multiplier()
            risk_per_trade = risk_per_trade * kelly_mult
            logger.debug(
                f"Kelly multiplier={kelly_mult:.4f} → risk_per_trade={risk_per_trade:.6f}"
            )

        # Fix #9: Escalar riesgo según score de la señal
        # Score 85-110 → riesgo completo
        # Score 72-84  → 75% del riesgo
        # Score 65-71  → 50% del riesgo
        # Score <65    → no operar (score = 0 significa señal sin score, se permite)
        if score >= 85:
            score_factor = 1.0
        elif score >= 72:
            score_factor = 0.75
        elif score >= 65:
            score_factor = 0.50
        elif score > 0:
            # Score positivo pero bajo (<65): riesgo mínimo
            score_factor = 0.25
        else:
            # score == 0: señal legacy o sin score, aplicar riesgo completo
            score_factor = 1.0

        if score_factor < 1.0:
            logger.debug(
                f"Score factor={score_factor:.2f} (score={score}) → "
                f"risk_per_trade={risk_per_trade:.6f} → {risk_per_trade * score_factor:.6f}"
            )
        risk_per_trade = risk_per_trade * score_factor

        # Fix #5: Modo degradado por drawdown severo
        dd_scale = await self.get_drawdown_risk_scale()
        if dd_scale < 1.0:
            logger.warning(
                f"⚠️ Modo degradado activo: dd_scale={dd_scale:.2f} "
                f"(DD actual > 50% del límite)"
            )
        risk_per_trade = risk_per_trade * dd_scale

        leverage = fase_cfg.get("leverage", DEFAULT_LEVERAGE)

        risk_usdt = balance * risk_per_trade
        sl_distance = abs(entry_price - sl)

        if sl_distance == 0:
            return 0, 0, "SL distance is zero"

        # Calcular qty teórica
        qty = risk_usdt / sl_distance

        # Obtener filtros reales del exchange (usar cache si está disponible)
        if self._cached_instrument_info is not None:
            info = self._cached_instrument_info
        else:
            info = await self.data.get_instrument_info()
            self._cached_instrument_info = info
        step = float(info.get('qtyStep', 0.1))
        min_qty = float(info.get('minOrderQty', 1.0))
        max_qty = float(info.get('maxOrderQty', 500000.0))
        min_notional = float(info.get('minNotionalValue', 1.0))
        max_notional = float(info.get('maxNotionalValue', 1000000.0))
        decimales = decimal_places_for_qty_step(step)

        # Ajustar al step size
        if step > 0:
            qty = math.floor(qty / step) * step
            qty = round(qty, decimales)

        # Respetar mínimos y máximos de cantidad
        qty = max(min_qty, min(qty, max_qty))

        # Validar valor nocional
        notional = qty * entry_price
        if notional < min_notional:
            qty = min_notional / entry_price
            qty = math.floor(qty / step) * step if step > 0 else qty
            qty = round(qty, decimales)
        if notional > max_notional:
            qty = max_notional / entry_price
            qty = math.floor(qty / step) * step if step > 0 else qty
            qty = round(qty, decimales)

        # Validación de margen (mantener)
        notional = qty * entry_price
        margin = notional / leverage
        max_margin = balance * 0.8
        if margin > max_margin:
            qty = (max_margin * leverage) / entry_price
            if step > 0:
                qty = math.floor(qty / step) * step
            logger.debug(f"Posición ajustada por margen: qty={qty}")

        if qty <= 0:
            return 0, 0, "Cantidad calculada es 0"

        return qty, risk_usdt, "OK"

    async def validate_sl_distance(self, entry: float, sl: float) -> bool:
        """Verifica que la distancia del SL no exceda el máximo de la fase (async)."""
        _, fase_cfg = await self.get_phase_config()
        max_sl_pct = fase_cfg.get("max_sl_pct", DEFAULT_MAX_SL_PCT)
        sl_pct = abs(entry - sl) / entry
        return bool(sl_pct <= max_sl_pct)

    async def validate_rr(self, entry: float, tp2: float, sl: float,
                          commission_pct: float = _DEFAULT_COMMISSION_PCT) -> bool:
        """
        Verifica que el Risk/Reward neto mínimo se cumpla (async).
        Fix #4: Descuenta comisiones de entrada+salida del RR bruto.
        FIX v5.1: Usa tp1_rr como referencia (no tp2_rr) porque:
          - TP1 es donde el 65% de la posición cierra
          - Después de TP1 el SL va a breakeven (con offset)
          - TP2 es bonus para el 35% restante
          - tp2_rr es muy exigente para capital pequeño debido a comisiones

        El RR neto se calcula como:
            rr_neto = (tp_dist - commission) / (sl_dist + commission)

        donde commission = entry_price * commission_pct.

        Args:
            entry: Precio de entrada.
            tp2: Precio de take profit 2.
            sl: Precio de stop loss.
            commission_pct: Comisión total esperada (entrada + salida) como fracción.
                            Default 0.0011 = 0.11% (0.055% taker × 2).
        """
        _, fase_cfg = await self.get_phase_config()
        min_rr = fase_cfg.get("tp1_rr", 1.5)  # Usar tp1_rr como referencia
        sl_dist = abs(entry - sl)
        tp_dist = abs(tp2 - entry)

        if sl_dist == 0:
            return False

        # Fix #4: RR neto descontando comisiones
        commission_abs = entry * commission_pct
        sl_net = sl_dist + commission_abs
        tp_net = tp_dist - commission_abs

        # Si el tp neto es negativo, el trade es imposible de ganar post-comisiones
        if tp_net <= 0:
            logger.debug(
                f"validate_rr | RR neto negativo (tp_dist={tp_dist:.6f} comm={commission_abs:.6f})"
            )
            return False

        rr_neto = tp_net / sl_net

        logger.debug(
            f"validate_rr | entry={entry:.6f} tp2={tp2:.6f} sl={sl:.6f} | "
            f"rr_bruto={tp_dist/sl_dist:.4f} rr_neto={rr_neto:.4f} "
            f"min_rr_config(tp1_rr)={min_rr} | comm={commission_pct:.4f}"
        )

        # Tolerancia del 10% + colchón neto mínimo
        MIN_NET_RR = 1.10  # RR neto mínimo absoluto (colchón del 10% sobre breakeven)
        threshold = max(min_rr * 0.90, MIN_NET_RR)
        return bool(rr_neto >= threshold)

    async def distribute_tp(self, qty_total: float) -> tuple[float, float]:
        """Divide la cantidad entre TP1 y TP2 según la fase (async)."""
        _, fase_cfg = await self.get_phase_config()
        tp1_pct = fase_cfg.get("tp1_porcentaje", 0.5)
        qty_tp1 = round(qty_total * tp1_pct, 4)
        qty_tp2 = round(qty_total - qty_tp1, 4)
        return qty_tp1, qty_tp2

    # ── Gestión de Riesgo ──────────────────────────────────────
    def set_notifier(self, notifier: Any) -> None:
        self._notifier = notifier

    async def reset_daily_if_needed(self) -> None:
        """Resetear contadores diarios semanales y mensuales según corresponda.
        
        Fix #1: Detecta cambios de semana y mes para reiniciar capitales de referencia.
        Fix #3: Resetea contador de pérdidas consecutivas al cambiar de día.
        Fix #6: Reconoce discrepancia de balance contra el exchange.
        """
        from config.loader import TIMEZONE_NAME
        try:
            tz = pytz.timezone(TIMEZONE_NAME)
        except Exception:
            tz = pytz.timezone("America/Asuncion")
        ahora = datetime.now(tz)
        hoy = ahora.date()
        # Fix #1: Número de semana ISO y mes
        iso_calendar = hoy.isocalendar()
        semana_nueva = iso_calendar[1]
        mes_nuevo = ahora.month

        # Obtener balance actual para reconciliación
        if hasattr(self, "_backtest_balance") and self._backtest_balance is not None:
            balance_actual = self._backtest_balance
        else:
            try:
                balance_actual = await self.data.get_balance()
            except Exception:
                balance_actual = None

        # ── Cambio de día ──
        if self.fecha_dia != hoy:
            self.fecha_dia = hoy
            self.stops_dia = 0
            # Fix #3: Reset de pérdidas consecutivas al nuevo día
            self._consecutive_losses = 0
            self._loss_pause_until = 0.0
            self._peak_balance_daily = None
            self._peak_balance_session = None
            self._cached_fase_num = None
            self._cached_fase_cfg = None

            if self.capital_inicio_dia is None:
                if balance_actual is not None:
                    self.capital_inicio_dia = balance_actual

        # ── Fix #1: Cambio de semana ──
        if self._semana_actual is None or self._semana_actual != semana_nueva:
            self._semana_actual = semana_nueva
            if balance_actual is not None:
                # Si ya teníamos capital_inicio_semana, calcular DD semanal acumulado
                if self.capital_inicio_semana is not None:
                    dd_semanal = (self.capital_inicio_semana - balance_actual) / self.capital_inicio_semana
                    # Loggear DD semanal al cerrar la semana
                    logger.info(
                        f"📊 Semana {semana_nueva - 1} cerrada: "
                        f"DD semanal={dd_semanal*100:.2f}%"
                    )
                self.capital_inicio_semana = balance_actual

        # ── Fix #1: Cambio de mes ──
        if self._mes_actual is None or self._mes_actual != mes_nuevo:
            self._mes_actual = mes_nuevo
            if balance_actual is not None:
                if self.capital_inicio_mes is not None:
                    dd_mensual = (self.capital_inicio_mes - balance_actual) / self.capital_inicio_mes
                    logger.info(
                        f"📊 Mes {mes_nuevo - 1} cerrado: "
                        f"DD mensual={dd_mensual*100:.2f}%"
                    )
                self.capital_inicio_mes = balance_actual

        # ── Fix #6: Reconciliación de balance real vs esperado ──
        if (balance_actual is not None
                and self.capital_inicio_dia is not None
                and self.capital_inicio_dia > 0):
            diff_pct = abs(balance_actual - self.capital_inicio_dia) / self.capital_inicio_dia
            if diff_pct > _BALANCE_DISCREPANCY_THRESHOLD:
                logger.error(
                    f"⚠️ Discrepancia de balance detectada: "
                    f"bot esperaba ${self.capital_inicio_dia:.2f}, "
                    f"exchange reporta ${balance_actual:.2f} "
                    f"(diff {diff_pct*100:.1f}%)"
                )
                # Actualizar capital_inicio_dia al valor real del exchange
                self.capital_inicio_dia = balance_actual
                if self._notifier:
                    try:
                        self._notifier.send(
                            "⚠️ Discrepancia de balance",
                            f"Diferencia del {diff_pct*100:.1f}% entre balance esperado y real. "
                            f"Capital ajustado a ${balance_actual:.2f}",
                            severity="WARNING"
                        )
                    except Exception:
                        pass

    async def get_current_drawdown_pct(self) -> float:
        """
        Calcula el drawdown actual del día como porcentaje desde el peak.
        Retorna 0 si no hay datos suficientes.
        También rastrea drawdown por sesión de trading.
        """
        _, _fase_cfg = await self.get_phase_config()
        if hasattr(self, "_backtest_balance") and self._backtest_balance is not None:
            current_balance = self._backtest_balance
        else:
            current_balance = await self.data.get_balance()

        # Actualizar peak diario
        if not hasattr(self, '_peak_balance_daily') or self._peak_balance_daily is None:
            self._peak_balance_daily = current_balance
        elif current_balance > self._peak_balance_daily:
            self._peak_balance_daily = current_balance

        # Actualizar peak por sesión de trading
        if not hasattr(self, '_peak_balance_session') or self._peak_balance_session is None:
            self._peak_balance_session = current_balance
        elif current_balance > self._peak_balance_session:
            self._peak_balance_session = current_balance

        if self._peak_balance_daily and self._peak_balance_daily > 0:
            dd_pct = (self._peak_balance_daily - current_balance) / self._peak_balance_daily * 100
            return max(0, dd_pct)
        return 0.0

    async def get_session_drawdown_pct(self) -> float:
        """
        Calcula el drawdown de la sesión de trading actual.
        Útil para límites más estrictos dentro de una misma sesión.
        """
        if not hasattr(self, '_peak_balance_session') or self._peak_balance_session is None:
            return 0.0
        if hasattr(self, "_backtest_balance") and self._backtest_balance is not None:
            current_balance = self._backtest_balance
        else:
            try:
                current_balance = await self.data.get_balance()
            except Exception:
                return 0.0
        if self._peak_balance_session > 0:
            dd_pct = (self._peak_balance_session - current_balance) / self._peak_balance_session * 100
            return max(0, dd_pct)
        return 0.0

    async def get_drawdown_risk_scale(self) -> float:
        """
        Fix #5: Retorna un multiplicador de riesgo (0-1) según la severidad del DD actual.

        Si el DD actual supera el 50% del límite diario, se reduce el riesgo progresivamente:
        - DD < 50% del límite → scale = 1.0 (riesgo completo)
        - DD 50-75%         → scale = 0.5 (mitad del riesgo)
        - DD > 75%          → scale = 0.25 (cuarto del riesgo)
        - DD >= límite      → scale = 0.0 (debería haber lanzado RiskException)

        Returns:
            float: 0.0 a 1.0 indicando la fracción del riesgo base a aplicar.
        """
        _, fase_cfg = await self.get_phase_config()
        max_dd_daily = fase_cfg.get("max_dd_daily", 0.06) * 100  # convertir a %

        if max_dd_daily <= 0:
            return 1.0

        try:
            current_dd = await self.get_current_drawdown_pct()
        except Exception:
            return 1.0

        ratio = current_dd / max_dd_daily if max_dd_daily > 0 else 0

        if ratio < 0.50:
            return 1.0
        elif ratio < 0.75:
            return 0.5
        elif ratio < 1.0:
            return 0.25
        else:
            # Ya debería haber sido bloqueado por validate_drawdown()
            return 0.0

    async def validate_drawdown(self) -> None:
        """
        Verifica que el drawdown diario no exceda el máximo configurado.
        También verifica drawdown por sesión (más estricto: 80% del límite diario).
        Fix #1: También verifica drawdown semanal y mensual.
        Lanza RiskException si se supera algún límite.
        """
        _, fase_cfg = await self.get_phase_config()
        max_dd_daily = fase_cfg.get("max_dd_daily", 0.06) * 100  # Convertir a %
        # Fix #1: Límites semanal y mensual
        max_dd_weekly = fase_cfg.get("max_dd_weekly", 0.0) * 100
        max_dd_monthly = fase_cfg.get("max_dd_monthly", 0.0) * 100

        if hasattr(self, "_backtest_balance") and self._backtest_balance is not None:
            current_balance = self._backtest_balance
        else:
            current_balance = await self.data.get_balance()

        # 1. Validar drawdown diario
        current_dd = await self.get_current_drawdown_pct()
        if current_dd >= max_dd_daily:
            raise RiskException(
                f"Drawdown diario máximo alcanzado: {current_dd:.2f}% (límite: {max_dd_daily:.2f}%)"
            )

        # 2. Validar drawdown por sesión (80% del límite diario)
        max_dd_session = max_dd_daily * 0.8
        session_dd = await self.get_session_drawdown_pct()
        if session_dd >= max_dd_session:
            raise RiskException(
                f"Drawdown de sesión máximo alcanzado: {session_dd:.2f}% "
                f"(límite sesión: {max_dd_session:.2f}%)"
            )

        # 3. Fix #1: Validar drawdown semanal
        if max_dd_weekly > 0 and self.capital_inicio_semana is not None and self.capital_inicio_semana > 0:
            dd_semanal = (self.capital_inicio_semana - current_balance) / self.capital_inicio_semana * 100
            if dd_semanal >= max_dd_weekly:
                raise RiskException(
                    f"Drawdown semanal máximo alcanzado: {dd_semanal:.2f}% "
                    f"(límite: {max_dd_weekly:.2f}%)"
                )

        # 4. Fix #1: Validar drawdown mensual
        if max_dd_monthly > 0 and self.capital_inicio_mes is not None and self.capital_inicio_mes > 0:
            dd_mensual = (self.capital_inicio_mes - current_balance) / self.capital_inicio_mes * 100
            if dd_mensual >= max_dd_monthly:
                raise RiskException(
                    f"Drawdown mensual máximo alcanzado: {dd_mensual:.2f}% "
                    f"(límite: {max_dd_monthly:.2f}%)"
                )

    async def register_stop(self) -> None:
        """
        Registra un stop loss ejecutado.

        Fix #3: Trackea pérdidas consecutivas. Si se alcanza el umbral,
        activa una pausa forzada de 2 horas y notifica.
        """
        self.stops_dia += 1
        # Fix #3: Incrementar contador de pérdidas consecutivas
        self._consecutive_losses += 1

        _, fase_cfg = await self.get_phase_config()
        max_stops = fase_cfg.get("max_stops_day", 4)
        max_consecutive = fase_cfg.get("max_consecutive_losses", 3)

        if self.stops_dia >= max_stops:
            raise RiskException(
                f"Límite de stops diarios alcanzado: {self.stops_dia}/{max_stops}"
            )

        # Fix #3: Circuit breaker por pérdidas consecutivas
        if self._consecutive_losses >= max_consecutive:
            import time
            pausa_segundos = 7200  # 2 horas
            self._loss_pause_until = time.monotonic() + pausa_segundos
            msg = (
                f"🔴 Circuit breaker activado: {self._consecutive_losses} pérdidas "
                f"consecutivas. Pausa de {pausa_segundos // 3600}h."
            )
            logger.error(msg)
            if self._notifier:
                try:
                    self._notifier.send(
                        "🔴 Circuit Breaker",
                        msg,
                        severity="CRITICAL"
                    )
                except Exception:
                    pass
            raise RiskException(msg)

        # También verificar drawdown al registrar un stop
        await self.validate_drawdown()

    async def is_loss_paused(self) -> bool:
        """
        Fix #3: Verifica si el bot está en pausa por pérdidas consecutivas.

        Returns:
            True si el bot debe esperar antes de abrir nuevas posiciones.
        """
        if self._loss_pause_until <= 0:
            return False
        import time
        if time.monotonic() < self._loss_pause_until:
            remaining = int(self._loss_pause_until - time.monotonic())
            logger.debug(f"Loss pause activa: {remaining // 60}min restantes")
            return True
        # Pausa expirada, resetear
        self._loss_pause_until = 0.0
        return False

    async def update_balance_for_drawdown(self, current_balance: float) -> None:
        """
        Actualiza el balance para tracking de drawdown (llamado desde run_cycle).
        Actualiza tanto el peak diario como el de sesión.
        """
        if not hasattr(self, '_peak_balance_daily') or self._peak_balance_daily is None:
            self._peak_balance_daily = current_balance
        elif current_balance > self._peak_balance_daily:
            self._peak_balance_daily = current_balance

        # También actualizar peak de sesión
        if not hasattr(self, '_peak_balance_session') or self._peak_balance_session is None:
            self._peak_balance_session = current_balance
        elif current_balance > self._peak_balance_session:
            self._peak_balance_session = current_balance

    async def is_trading_session(self) -> bool:

        try:
            tz = pytz.timezone(TIMEZONE_NAME)
        except Exception:
            tz = pytz.timezone("America/Asuncion")

        ahora = datetime.now(tz)
        hora_actual = ahora.hour

        _, fase_cfg = await self.get_phase_config()
        inicio = fase_cfg.get("sesion_inicio", 14)
        fin = fase_cfg.get("sesion_fin", 22)

        # Si fin < inicio, asumimos que cruza la medianoche, pero en nuestro caso no es necesario
        return bool(inicio <= hora_actual < fin)
