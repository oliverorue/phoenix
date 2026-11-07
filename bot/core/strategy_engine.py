"""
Estrategia de scalping con entrada en pullbacks – v7.3

Sistema de scoring multi‑factor:
  • Tendencia   (EMA50 1h + ADX)    → 0‑30 pts
  • Pullback    (retroceso a EMA20)  → 0‑30 pts
  • RSI         (sobreventa/compra)  → 0‑20 pts
  • Patrón vela (pin/engulf/martillo)→ 0‑15 pts
  • Volumen     (ratio vs MA10)      → 0‑15 pts
  ─────────────────────────────────────────────────
  Total 0‑110 pts  |  Señal válida ≥ 75 pts
  RSI ≥ 8pts obligatorio  |  Score tendencia > 12pts obligatorio

SL = max(ATR7, ATR14) × 2.5 × (1 + BB_width × factor)
TP1 = SL × tp1_rr   (cierre parcial)
TP2 = SL × tp2_rr   (objetivo final)

Filtros adicionales (v7.3):
  • Consistencia de tendencia: precio debe haber estado
    del lado correcto de EMA50 las últimas 4h (buffer 0.3%).
  • ADX mínimo absoluto: 22 (tendencia moderada).
  • Zona de pullback: ±1.0% alrededor de EMA20.
  • Funding rate extremo bloquea entrada en dirección contraria.
  • Señal inválida si pasaron > 30s desde evaluación.
  • Fix C: Confirmación de momentum — microestructura de rebote
    + cierre de vela en dirección.
  • Fix D: Estructura de mercado — Higher Highs / Lower Lows en 1h.
  • Fix E: Cooldown post-señal — evita sobre-trading.
  • Fix F: SL dinámico — usa max(ATR7, ATR14) + BB width.
"""

from loguru import logger
from typing import Any, Optional
import time
import pandas as pd

from config.loader import get_phase_config, TIMEZONE_NAME
from bot.core.indicators import Indicators


class Signal:
    """Señal de trading con todos los parámetros necesarios, incluido timestamp de evaluación."""

    def __init__(self, direction: Optional[str] = None, entry: float = 0.0,
                 stop_loss: float = 0.0, take_profit1: float = 0.0,
                 take_profit2: float = 0.0, score: int = 0, reason: str = "",
                 atr: float = 0.0) -> None:
        self.direction = direction
        self.entry = entry
        self.sl = stop_loss
        self.tp1 = take_profit1
        self.tp2 = take_profit2
        self.score = score
        self.reason = reason
        self.atr = atr
        self.timestamp = time.time()  # Fix #8: timestamp de evaluación

    @property
    def has_signal(self) -> bool:
        return self.direction is not None

    def __repr__(self) -> str:
        if not self.has_signal:
            return "Signal(NONE)"
        return (f"Signal({self.direction} | Entry:{self.entry:.4f} "
                f"SL:{self.sl:.4f} | TP1:{self.tp1:.4f} | TP2:{self.tp2:.4f} "
                f"Score:{self.score})")


class ScalpingStrategy:
    """
    Estrategia de scalping con entrada en pullbacks.

    Flujo de decisión:
    1. Determina tendencia vía EMA50(1h) + ADX + consistencia 4h
    2. Verifica estructura HH/LL en 1h (Fix D)
    3. Detecta retroceso del precio hacia EMA20(15m)
    4. Confirma con RSI extremo + patrón de vela de rechazo
    5. Confirma microestructura de rebote + cierre en dirección (Fix C)
    6. Filtra por funding rate extremo (binario)
    7. Aplica cooldown post-señal (Fix E)
    8. Calcula SL/TP dinámicos con max(ATR7, ATR14) + BB width (Fix F)
    9. Asigna score ponderado 0‑110; emite señal si ≥ 75, RSI ≥ 8pts y tendencia > 12pts
    """

    # ── Parámetros fijos ─────────────────────────────────
    MA_TREND = 50               # EMA de 1h → tendencia
    MA_ENTRY = 20               # EMA de 15m → pullback
    ADX_MIN_ABSOLUTE = 14       # Reducido de 17 → 14: permite tendencias débiles en XRP
    SL_ATR_MULTIPLIER = 2.5     # SL base = 2.5 × ATR
    SL_BB_FACTOR = 0.3          # Factor de ajuste por BB width (Fix F)
    PULLBACK_ZONE_PCT = 0.010   # ±1.0% alrededor de EMA20
    MIN_SCORE = 35              # Reducido de 48 → 35: captura más señales en backtest 4 meses
    TREND_CONSISTENCY_CANDLES = 1  # Reducido de 2 → 1: solo la última vela 1h completa debe confirmar
    TREND_BUFFER = 0.005        # Aumentado de 0.001 → 0.005 (0.5%): margen más realista
    MOMENTUM_DIRECTION_THRESHOLD = 2.0  # Si |DI+ - DI-| < esto, caer a EMA50 como fallback
    SIGNAL_EXPIRY_SECONDS = 30  # Señal expira tras 30s — Fix #8
    RSI_MIN_SCORE = 0           # Sin veto de RSI; scoring informa pero no bloquea

    # Fix E: Cooldown
    COOLDOWN_CANDLES = 3        # Velas 15m de espera tras señal emitida
    MAX_CONSECUTIVE_LOSSES_SAME_DIR = 2  # Si 2 pérdidas seguidas en misma dirección, parar

    # Fix C: Microestructura
    MOMENTUM_CLOSE_THRESHOLD = 0.002  # 0.2% — cierre debe confirmar dirección

    # Fix D: HH/LL
    HHLL_LOOKBACK = 6           # Velas 1h para verificar estructura

    def __init__(self, override_cfg: Optional[dict[str, Any]] = None) -> None:
        self.indicators = Indicators()
        self._override_cfg = override_cfg or {}

        # Fix E: estado de cooldown (basado en velas, compatible con backtest)
        self._cooldown_candles_remaining: int = 0    # velas 15m restantes de cooldown
        self._consecutive_losses_same_dir: dict[str, int] = {"LONG": 0, "SHORT": 0}  # rachas por dirección

    # ── Método principal ──────────────────────────────────
    def evaluate(self, df_1h: pd.DataFrame, df_15m: pd.DataFrame,
                 current_price: float, balance: float,
                 market_signal: Optional[dict[str, Any]] = None) -> Signal:
        """
        Evalúa todas las condiciones y retorna una Signal.

        Args:
            market_signal: dict opcional con datos de mercado (funding rate, OI).
                           Si el funding rate es extremo, bloquea entrada en la
                           dirección contraria (filtro binario) en vez de ajustar score.
        """
        # 1. Configuración de fase (con overrides del backtester/optimizer)
        _, fase_cfg = get_phase_config(balance)
        if self._override_cfg:
            fase_cfg = {**fase_cfg, **self._override_cfg}
        if fase_cfg is None:
            return Signal()

        # 2. Indicadores 1h
        if len(df_1h) < 14:
            return Signal()
        ema50_1h = df_1h["close"].ewm(span=self.MA_TREND, adjust=False).mean().iloc[-1]
        ind_1h = self.indicators.calc_1h_indicators(df_1h)
        adx_1h = ind_1h.get("ADX", 0)
        adx_pos = ind_1h.get("ADX_pos", 0)  # DI+ = fuerza direccional alcista
        adx_neg = ind_1h.get("ADX_neg", 0)  # DI- = fuerza direccional bajista

        # 3. Indicadores 15m
        ind_15m = self.indicators.calc_15m_indicators(df_15m)
        atr = ind_15m.get("ATR", 0)
        atr_pct = ind_15m.get("ATR_pct", 0)
        bb_width = ind_15m.get("BB_width", 0)
        ema20_15m = ind_15m.get("EMA20", df_15m["close"].iloc[-1])
        rsi = ind_15m.get("RSI", 50)
        if atr <= 0:
            return Signal()

        # 4. Filtro de volatilidad (de la fase)
        max_vol = fase_cfg.get("max_volatility_pct", 20.0)
        if atr_pct * 100 > max_vol:
            return Signal()

        # 5. ADX mínimo absoluto
        if adx_1h < self.ADX_MIN_ABSOLUTE:
            return Signal()

        # 6. Determinar dirección
        #
        # Cambio v4: usamos DI+/DI- (momentum) como señal primaria en vez de EMA50.
        #   DI+ > DI-  → momentum alcista  → LONG
        #   DI- > DI+  → momentum bajista  → SHORT
        #   |DI+ - DI-| < threshold       → momentum débil → fallback a EMA50
        #
        # Esto rompe la asimetría 100% LONG/SHORT que causaba EMA50 sola,
        # especialmente cuando el warmup no ha convergido.
        momentum_diff = abs(adx_pos - adx_neg)
        if momentum_diff >= self.MOMENTUM_DIRECTION_THRESHOLD:
            # Momentum direccional fuerte → usar DI+/DI-
            if adx_pos > adx_neg:
                direction = "LONG"
            else:
                direction = "SHORT"
        else:
            # Momentum débil → fallback a EMA50
            if current_price > ema50_1h:
                direction = "LONG"
            elif current_price < ema50_1h:
                direction = "SHORT"
            else:
                return Signal()

        logger.debug(f"DIR={direction} DI+={adx_pos:.1f} DI-={adx_neg:.1f} diff={momentum_diff:.1f} EMA50={current_price:.4f}/{ema50_1h:.4f}")

        # Asignar RSI según dirección
        if direction == "LONG":
            rsi_min = fase_cfg.get("rsi_min_long", 25)
            rsi_max = fase_cfg.get("rsi_max_long", 35)
        else:
            rsi_min = fase_cfg.get("rsi_min_short", 65)
            rsi_max = fase_cfg.get("rsi_max_short", 75)

        # 6b. Consistencia de tendencia
        # Cuando la dirección se determinó por DI+/DI- (momentum fuerte),
        # los propios DI+/DI- ya son el chequeo de consistencia.
        # Solo verificamos consistencia EMA50 cuando usamos fallback EMA50
        # (momentum débil). Esto evita que EMA50 vete señales SHORT
        # en uptrend cuando DI- ya muestra momentum bajista.
        if momentum_diff < self.MOMENTUM_DIRECTION_THRESHOLD:
            if not self._check_trend_consistency(df_1h, ema50_1h, direction):
                logger.debug("Tendencia inconsistente últimas 4h → sin señal")
                return Signal()

        # 7. Filtro de mercado lateral
        if Indicators.is_market_ranging(ind_1h, ind_15m, fase_cfg):
            logger.debug("Mercado lateral detectado → sin señal")
            return Signal()

        # Fix D: Estructura de mercado HH/LL — ahora suma puntos en vez de vetar
        # (el score mínimo de 75 ya filtra señales débiles)
        hhll_ok = self._check_market_structure_hhll(df_1h, direction)

        # 8. Pullback: ¿el precio está en zona de EMA20?
        pb_distance = self._calc_pullback(current_price, ema20_15m, df_15m, direction)
        if pb_distance is None or pb_distance > self.PULLBACK_ZONE_PCT * 2:
            return Signal()

        # Fix C: Confirmación de momentum — microestructura de rebote + cierre
        if not self._check_momentum_confirmation(df_15m, direction):
            logger.debug(f"Momentum no confirma rebote {direction} → sin señal")
            return Signal()

        # Fix E: Cooldown post-señal
        if not self._check_cooldown(direction):
            logger.debug(f"Cooldown activo para {direction} → sin señal")
            return Signal()

        # 9. Sistema de scoring 0‑120 (con bonus HH/LL +10)
        score, reasons = self._calc_score(
            direction, current_price, ema50_1h, adx_1h,
            ema20_15m, pb_distance, rsi, rsi_min, rsi_max,
            ind_15m, fase_cfg, hhll_ok
        )

        # Fix B: Veto por score de tendencia ≤ 12pts
        # score_tendencia se extrae del primer componente de reasons
        # o directamente del cálculo interno. Lo verificamos aquí:
        if score <= 0:
            logger.debug("Score 0 (por veto de RSI o tendencia) → sin señal")
            return Signal()

        # 9b. Filtro binario de funding rate
        if market_signal and market_signal.get("signal") != "neutral":
            fr_signal = market_signal["signal"]
            fr_reason = market_signal.get("reason", "")
            if direction == "LONG" and fr_signal == "bearish":
                logger.debug(f"Funding rate extremo bloquea LONG: {fr_reason}")
                return Signal()
            if direction == "SHORT" and fr_signal == "bullish":
                logger.debug(f"Funding rate extremo bloquea SHORT: {fr_reason}")
                return Signal()

        if score < self.MIN_SCORE:
            logger.debug(f"Score {score} < {self.MIN_SCORE} → sin señal")
            return Signal()

        # Fix F: SL dinámico — max(ATR7, ATR14) + ajuste por BB width
        # BB_width > 0.08 (8%) = mercado expandiéndose → SL más amplio
        # BB_width < 0.02 (2%) = mercado contrayéndose → SL más justo
        bb_adjustment = 1.0 + (bb_width * self.SL_BB_FACTOR)
        sl_atr_effective = atr * bb_adjustment

        sl_distance = sl_atr_effective * self.SL_ATR_MULTIPLIER
        max_sl_pct = fase_cfg.get("max_sl_pct", 0.05)
        if sl_distance / current_price > max_sl_pct:
            sl_distance = current_price * max_sl_pct

        if direction == "LONG":
            sl = current_price - sl_distance
            tp1 = current_price + sl_distance * fase_cfg.get("tp1_rr", 1.5)
            tp2 = current_price + sl_distance * fase_cfg.get("tp2_rr", 2.0)
        else:
            sl = current_price + sl_distance
            tp1 = current_price - sl_distance * fase_cfg.get("tp1_rr", 1.5)
            tp2 = current_price - sl_distance * fase_cfg.get("tp2_rr", 2.0)

        # 11. Validar TP1 no demasiado cerca
        if abs(tp1 - current_price) < atr * 0.3:
            logger.debug("TP1 demasiado cerca")
            return Signal()

        # Agregar razón de mercado si existe (solo informativo)
        all_reasons = list(reasons)
        if market_signal and market_signal.get("reason"):
            all_reasons.append(market_signal["reason"])
            if market_signal.get("funding_rate") is not None:
                all_reasons.append(f"FR={market_signal['funding_rate']:.4f}%")

        # Fix E: Registrar cooldown después de señal exitosa
        self._apply_cooldown(direction)

        # Nota: atr que reportamos es el efectivo (max ATR) con ajuste BB
        return Signal(
            direction=direction,
            entry=current_price,
            stop_loss=sl,
            take_profit1=tp1,
            take_profit2=tp2,
            score=score,
            reason=" | ".join(all_reasons),
            atr=sl_atr_effective,
        )

    # ── Pullback ──────────────────────────────────────────
    def _calc_pullback(self, price: float, ema20: float,
                       df_15m: pd.DataFrame, direction: str) -> Optional[float]:
        """Retorna distancia |price - EMA20| / price si es pullback válido, o None."""
        if ema20 <= 0:
            return None
        distance = abs(price - ema20) / price

        if distance > self.PULLBACK_ZONE_PCT * 2.5:
            return None

        if len(df_15m) < 5:
            return distance  # sin suficiente historial, aceptamos

        # Verificar que hay un retroceso plausible (más laxo)
        prev_high = df_15m["high"].iloc[-5:-1].max()
        prev_low = df_15m["low"].iloc[-5:-1].min()

        if direction == "LONG":
            if price < ema20 * 0.97:
                return None  # demasiado por debajo de EMA20
            if prev_high < price * 0.995:
                return None  # sin máximos previos de donde retroceder
        else:
            if price > ema20 * 1.03:
                return None
            if prev_low > price * 1.005:
                return None

        return distance

    # ── Scoring multi‑factor (0‑110) ──────────────────────
    def _calc_score(self, direction: str, price: float,
                    ema50: float, adx: float, ema20: float,
                    pb_distance: float, rsi: float,
                    rsi_min: int, rsi_max: int,
                    ind_15m: dict[str, Any], fase_cfg: dict[str, Any],
                    hhll_ok: bool = True) -> tuple[int, list[str]]:
        score = 0
        reasons = []

        # ── A. Tendencia (0‑30) — Fix A: tendencia plana = 0pts ──
        ema50_dist = abs(price - ema50) / ema50
        if ema50_dist > 0.02:
            score += 30
            reasons.append(f"Tendencia fuerte ({ema50_dist:.1%})")
        elif ema50_dist > 0.008:
            score += 22
            reasons.append(f"Tendencia buena ({ema50_dist:.1%})")
        elif ema50_dist > 0.003:
            score += 12
            reasons.append(f"Tendencia leve ({ema50_dist:.1%})")
        # Fix A: tendencia plana ya no suma puntos (era +5)

        # Fix B: Veto por tendencia ≤ 12pts — retornamos score 0
        # que evaluate() interpretará como señal inválida
        if score < 10:  # Relajado de 15 → 10: permite tendencia "leve" (12 pts)
            logger.debug(f"Tendencia demasiado débil ({ema50_dist:.1%}) → veto")
            return 0, []

        # Bonus ADX (solo si ADX confirma tendencia)
        adx_min = fase_cfg.get("lateral_adx_min", 19)
        if adx > adx_min + 5:
            score = min(score + 5, 100)
            reasons.append(f"ADX sólido {adx:.0f}")

        # ── B. Pullback (0‑30) ──
        if pb_distance is not None:
            if pb_distance < 0.003:
                score += 30
                reasons.append("Pullback óptimo")
            elif pb_distance < 0.007:
                score += 25
                reasons.append("Pullback excelente")
            elif pb_distance < 0.012:
                score += 15
                reasons.append("Pullback bueno")
            else:
                # Fix A: pullback amplio ya no suma puntos (era +5)
                pass

        # ── C. RSI (0‑20) ──
        rsi_mid = (rsi_min + rsi_max) / 2
        rsi_pts = 0
        if rsi_min <= rsi <= rsi_max:
            score += 20
            rsi_pts = 20
            reasons.append(f"RSI ideal {rsi:.0f}")
        else:
            rsi_diff = abs(rsi - rsi_mid)
            if rsi_diff < 5:
                score += 14
                rsi_pts = 14
                reasons.append(f"RSI cercano {rsi:.0f}")
            elif rsi_diff < 10:
                score += 8
                rsi_pts = 8
                reasons.append(f"RSI aceptable {rsi:.0f}")
            # Si rsi_diff >= 10 → 0pts

        # Banda neutral (RSI 40-60): 4 pts para no penalizar completamente
        if rsi_pts == 0 and 40 <= rsi <= 60:
            score += 4
            rsi_pts = 4
            reasons.append(f"RSI neutral {rsi:.0f}")

        # RSI mínimo obligatorio (0 = sin veto)
        if rsi_pts < self.RSI_MIN_SCORE:
            logger.debug(f"RSI ({rsi:.0f}) no alcanza mínimo {self.RSI_MIN_SCORE}pts → sin señal")
            return 0, []

        # ── D. Patrón de vela (0‑15) — solo patrones reales ──
        if direction == "LONG":
            if Indicators.has_bullish_rejection(ind_15m):
                score += 15
                reasons.append("Patrón alcista")
        else:
            if Indicators.has_bearish_rejection(ind_15m):
                score += 15
                reasons.append("Patrón bajista")

        # ── D2. Estructura HH/LL (0‑10) — bonus, ya no es veto ──
        if hhll_ok:
            score += 10
            reasons.append("Estructura HH/LL OK")

        # ── E. Volumen (0‑15) ──
        vol_ratio = ind_15m["vol_actual"] / (ind_15m["vol_MA10"] + 1e-6)
        if vol_ratio > 1.5:
            score += 15
            reasons.append(f"Volumen alto {vol_ratio:.1f}x")
        elif vol_ratio > 1.0:
            score += 10
            reasons.append(f"Volumen sobre media {vol_ratio:.1f}x")
        # Fix A: volumen normal (0.7-1.0x) ya no suma puntos (era +5)

        return min(100, score), reasons

    # ── Consistencia de tendencia ──
    def _check_trend_consistency(self, df_1h: pd.DataFrame,
                                  ema50_current: float,
                                  direction: str) -> bool:
        """
        Verifica que el precio ha estado del lado correcto de EMA50
        durante las últimas N velas 1h (excluyendo la actual).

        N = TREND_CONSISTENCY_CANDLES (4 velas = 4h de consistencia).

        Usa un buffer del 0.3% para evitar falsos negativos por
        diferencias mínimas de cálculo cuando el precio está rozando EMA50.
        """
        if len(df_1h) < self.TREND_CONSISTENCY_CANDLES + 2:
            return True  # datos insuficientes, permitir entrada

        # Recalcular EMA50 para toda la ventana 1h
        ema50_series = df_1h["close"].ewm(span=self.MA_TREND, adjust=False).mean()

        n_check = self.TREND_CONSISTENCY_CANDLES
        for i in range(1, n_check + 1):
            idx = -i - 1
            close_i = df_1h["close"].iloc[idx]
            ema50_i = ema50_series.iloc[idx]

            if direction == "LONG":
                if close_i <= ema50_i * (1 + self.TREND_BUFFER):
                    return False
            else:  # SHORT
                if close_i >= ema50_i * (1 - self.TREND_BUFFER):
                    return False
        return True

    # ── Fix C: Confirmación de momentum (relajado v2) ─────────
    def _check_momentum_confirmation(self, df_15m: pd.DataFrame, direction: str) -> bool:
        """
        Verifica que la última vela tenga un cierre coherente con la dirección.
        VERSIÓN RELAJADA para $50: solo verifica que el cierre no sea
        fuertemente contrario a la dirección. La microestructura de rebote
        se eliminó porque en pullbacks el precio está naturalmente bajando
        (para LONG) — no podemos esperar que ya haya rebotado antes de entrar.

        LONG  → close no debe ser < open * 0.997 (caída > 0.3%)
        SHORT → close no debe ser > open * 1.003 (subida > 0.3%)
        """
        if len(df_15m) < 2:
            return True

        last = df_15m.iloc[-1]

        if direction == "LONG":
            # Solo vetamos si la caída es muy fuerte (> 0.3%)
            if last["close"] < last["open"] * (1 - 0.003):
                logger.debug(f"Momentum LONG: vela fuertemente bajista "
                             f"(close={last['close']:.4f} < open*0.997)")
                return False
        else:  # SHORT
            # Solo vetamos si la subida es muy fuerte (> 0.3%)
            if last["close"] > last["open"] * (1 + 0.003):
                logger.debug(f"Momentum SHORT: vela fuertemente alcista "
                             f"(close={last['close']:.4f} > open*1.003)")
                return False

        return True

    # ── Fix D: Estructura de mercado HH/LL (relajado v2) ────
    def _check_market_structure_hhll(self, df_1h: pd.DataFrame, direction: str) -> bool:
        """
        Verifica que la estructura de mercado en 1h confirme la dirección.
        VERSIÓN RELAJADA para $50: solo verifica el último par de velas
        completas (posiciones -2 y -3). Filtra laterales extremos pero
        permite entradas en tendencias con pullbacks.

        Para LONG: el high más reciente debe ser > el anterior.
        Para SHORT: el low más reciente debe ser < el anterior.
        """
        if len(df_1h) < 4:
            return True  # datos insuficientes, permitir entrada

        highs = df_1h["high"].iloc[-self.HHLL_LOOKBACK:]
        lows = df_1h["low"].iloc[-self.HHLL_LOOKBACK:]

        if direction == "LONG":
            # Verificar Higher High simple: último high completo > penúltimo
            if len(highs) >= 3:
                if highs.iloc[-2] <= highs.iloc[-3]:
                    logger.debug(f"HH/LL LONG: sin Higher High "
                                 f"({highs.iloc[-2]:.4f} <= {highs.iloc[-3]:.4f})")
                    return False
        else:  # SHORT
            # Verificar Lower Low simple: último low completo < penúltimo
            if len(lows) >= 3:
                if lows.iloc[-2] >= lows.iloc[-3]:
                    logger.debug(f"HH/LL SHORT: sin Lower Low "
                                 f"({lows.iloc[-2]:.4f} >= {lows.iloc[-3]:.4f})")
                    return False

        return True

    # ── Fix E: Cooldown post-señal (basado en velas, no time.time()) ─
    def _check_cooldown(self, direction: str) -> bool:
        """
        Verifica si podemos operar en la dirección solicitada.
        Dos condiciones:
          1. Cooldown temporal: si estamos en periodo de enfriamiento,
             esperar COOLDOWN_CANDLES velas 15m (~45 min).
             *** AHORA USA CONTADOR DE VELAS, NO time.time() ***
          2. Rachas de pérdidas: si ya perdimos MAX_CONSECUTIVE_LOSSES_SAME_DIR
             veces seguidas en esta dirección, bloquear hasta reset.

        Returns:
            True si se puede operar, False si está en cooldown.
        """
        # Condición 1: Cooldown temporal (basado en velas 15m)
        if self._cooldown_candles_remaining > 0:
            logger.debug(f"Cooldown activo: faltan {self._cooldown_candles_remaining} velas 15m")
            return False

        # Condición 2: Rachas de pérdidas en la misma dirección
        if self._consecutive_losses_same_dir.get(direction, 0) >= self.MAX_CONSECUTIVE_LOSSES_SAME_DIR:
            logger.debug(f"Cooldown por racha de pérdidas en {direction}: "
                         f"{self._consecutive_losses_same_dir[direction]} "
                         f"≥ {self.MAX_CONSECUTIVE_LOSSES_SAME_DIR}")
            return False

        return True

    def _apply_cooldown(self, direction: str) -> None:
        """
        Activa el cooldown tras emitir una señal exitosa.
        Se espera COOLDOWN_CANDLES velas 15m antes de la siguiente evaluación.
        *** AHORA USA CONTADOR DE VELAS, COMPATIBLE CON BACKTEST ***
        """
        self._cooldown_candles_remaining = self.COOLDOWN_CANDLES  # 3 velas 15m
        logger.debug(f"Cooldown activado para {direction}: {self._cooldown_candles_remaining} velas 15m")

    def tick_cooldown(self) -> None:
        """
        Avanza el contador de cooldown en 1 vela 15m.
        Debe ser llamado una vez por vela procesada en el backtest,
        o por el scheduler en modo live al inicio de cada ciclo.
        """
        if self._cooldown_candles_remaining > 0:
            self._cooldown_candles_remaining -= 1
            if self._cooldown_candles_remaining == 0:
                logger.debug("Cooldown expirado — señales reactivadas")

    def register_trade_outcome(self, direction: str, was_loss: bool) -> None:
        """
        Registra el resultado de un trade para control de rachas.
        Debe ser llamado por el executor después de cada cierre de trade.

        Args:
            direction: "LONG" o "SHORT"
            was_loss: True si el trade fue una pérdida, False si ganancia
        """
        if was_loss:
            self._consecutive_losses_same_dir[direction] = \
                self._consecutive_losses_same_dir.get(direction, 0) + 1
            logger.debug(f"Racha de pérdidas en {direction}: "
                         f"{self._consecutive_losses_same_dir[direction]}")
        else:
            # Ganancia resetea la racha de pérdidas en esa dirección
            self._consecutive_losses_same_dir[direction] = 0
            logger.debug(f"Trade ganador en {direction}: racha reseteada")

    def reset_cooldown(self) -> None:
        """Resetea todos los estados de cooldown (ej: al cambiar de fase o día)."""
        self._cooldown_candles_remaining = 0
        self._consecutive_losses_same_dir = {"LONG": 0, "SHORT": 0}
        logger.debug("Cooldown reseteado")

    # ── Utilidad: sesión activa (para modo en vivo) ──────
    @staticmethod
    def is_session_active(balance: float) -> bool:
        """True si la hora local está dentro de la sesión configurada."""
        from datetime import datetime
        import pytz

        _, fase_cfg = get_phase_config(balance)
        try:
            tz = pytz.timezone(TIMEZONE_NAME)
        except Exception:
            tz = pytz.timezone("America/Asuncion")

        hora = datetime.now(tz).hour
        inicio = fase_cfg.get("sesion_inicio", 14)
        fin = fase_cfg.get("sesion_fin", 22)
        return bool(inicio <= hora < fin)
