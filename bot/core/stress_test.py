"""
Stress Testing para Paper Trading — Simula eventos extremos de mercado.

Escenarios incluidos:
  - **flash_crash**: Caída vertical del 15-30% con recuperación parcial.
  - **gap_down**: Apertura muy por debajo del cierre previo.
  - **gap_up**: Apertura muy por encima del cierre previo.
  - **extreme_volatility**: Velas con rangos 5-10× mayores al normal.

Cada escenario prueba cómo responde el portfolio paper (SL, slippage,
drawdown) bajo condiciones de estrés que raramente se ven en backtests
con datos históricos normales.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
from loguru import logger


# ── Tipos de escenario ─────────────────────────────────────────────

class ScenarioType(Enum):
    FLASH_CRASH = "flash_crash"
    GAP_DOWN = "gap_down"
    GAP_UP = "gap_up"
    EXTREME_VOLATILITY = "extreme_volatility"


# ── Configuración de un escenario ──────────────────────────────────

@dataclass
class StressScenario:
    """Define un escenario de estrés de mercado.

    Args:
        scenario_type: Tipo de escenario (enum).
        label: Nombre legible (ej: "Flash Crash -15%").
        base_price: Precio de referencia desde el que parte el escenario.
        max_slippage_pct: Slippage máximo durante el evento (ej: 0.05 = 5%).
        gap_pct: Magnitud del gap (para gap_up/gap_down).
        crash_depth_pct: Profundidad de la caída (para flash_crash).
        crash_recovery_pct: Fracción recuperada tras el crash (0.0-1.0).
        crash_candles: Velas que dura la caída.
        recovery_candles: Velas que dura la recuperación.
        vol_multiplier: Multiplicador de rango (high-low) para extreme_volatility.
        fill_price_mode: Cómo se determina el precio de fill en gaps:
            "worst" → fill en el peor precio posible para la posición
            "open"  → fill en el precio de apertura del gap
    """
    scenario_type: ScenarioType
    label: str
    base_price: float = 0.50

    # Slippage dinámico
    max_slippage_pct: float = 0.05  # 5% durante el evento extremo
    normal_slippage_pct: float = 0.001  # 0.1% en condiciones normales

    # Flash crash
    crash_depth_pct: float = 0.20       # -20%
    crash_recovery_pct: float = 0.60    # recupera 60% de la caída
    crash_candles: int = 4              # 4 velas de caída (~1h en 15m)
    recovery_candles: int = 12          # 12 velas de recuperación (~3h)

    # Gap
    gap_pct: float = 0.05               # 5% de gap
    fill_price_mode: str = "worst"      # "worst" | "open"

    # Volatilidad extrema
    vol_multiplier: float = 8.0         # 8× el rango normal

    # Control de simulación
    pre_candles: int = 10    # velas estables antes del evento
    post_candles: int = 20   # velas estables después del evento
    noise_std: float = 0.001  # ruido aleatorio en velas estables


# ── Escenarios predefinidos ────────────────────────────────────────

#: Flash crash severo: -25% en 4 velas, recupera 50%
FLASH_CRASH_SEVERE = StressScenario(
    scenario_type=ScenarioType.FLASH_CRASH,
    label="Flash Crash Severo -25%",
    crash_depth_pct=0.25,
    crash_recovery_pct=0.50,
    max_slippage_pct=0.08,
)

#: Flash crash moderado: -15% en 3 velas, recupera 70%
FLASH_CRASH_MODERATE = StressScenario(
    scenario_type=ScenarioType.FLASH_CRASH,
    label="Flash Crash Moderado -15%",
    crash_depth_pct=0.15,
    crash_recovery_pct=0.70,
    crash_candles=3,
    max_slippage_pct=0.04,
)

#: Gap down: -5% overnight
GAP_DOWN_5PCT = StressScenario(
    scenario_type=ScenarioType.GAP_DOWN,
    label="Gap Down -5%",
    gap_pct=0.05,
    max_slippage_pct=0.03,
)

#: Gap up: +5% overnight
GAP_UP_5PCT = StressScenario(
    scenario_type=ScenarioType.GAP_UP,
    label="Gap Up +5%",
    gap_pct=0.05,
    max_slippage_pct=0.03,
)

#: Gap down severo: -10%
GAP_DOWN_10PCT = StressScenario(
    scenario_type=ScenarioType.GAP_DOWN,
    label="Gap Down -10%",
    gap_pct=0.10,
    max_slippage_pct=0.05,
)

#: Volatilidad extrema: 8× rango normal
VOLATILITY_EXTREME = StressScenario(
    scenario_type=ScenarioType.EXTREME_VOLATILITY,
    label="Volatilidad Extrema 8×",
    vol_multiplier=8.0,
    max_slippage_pct=0.06,
)


# ── Generador de velas de estrés ───────────────────────────────────

def generate_stress_candles(scenario: StressScenario) -> list[dict[str, float]]:
    """Genera una serie de velas OHLC que materializan el escenario.

    Returns:
        Lista de dicts con keys ``open``, ``high``, ``low``, ``close``.
    """
    candles: list[dict[str, float]] = []
    price = scenario.base_price
    noise = scenario.noise_std

    def _candle(open_: float, close_: float, high_: float, low_: float) -> dict[str, float]:
        return {"open": round(open_, 6), "close": round(close_, 6),
                "high": round(high_, 6), "low": round(low_, 6)}

    def _normal(open_: float, direction: int = 1) -> dict[str, float]:
        c = open_ * (1 + direction * abs(np_random(noise)))
        h = max(open_, c) * (1 + abs(np_random(noise * 0.5)))
        l = min(open_, c) * (1 - abs(np_random(noise * 0.5)))
        return _candle(open_, c, h, l)

    import random as _random
    _rng = _random.Random(42)

    def np_random(std: float) -> float:
        """Box-Muller normal approx."""
        u = _rng.random()
        v = _rng.random()
        return math.sqrt(-2.0 * math.log(u)) * math.cos(2.0 * math.pi * v) * std

    # ── Velas pre-evento (estables) ──
    for _ in range(scenario.pre_candles):
        c = _normal(price)
        candles.append(c)
        price = c["close"]

    # ── Velas del evento ──
    if scenario.scenario_type == ScenarioType.FLASH_CRASH:
        # Caída
        drop_per_candle = scenario.crash_depth_pct / scenario.crash_candles
        for _ in range(scenario.crash_candles):
            factor = 1.0 - drop_per_candle * (1 + abs(np_random(noise * 2)))
            close_ = price * factor
            low_ = min(price, close_) * (1 - abs(np_random(noise * 3)))
            high_ = max(price, close_) * (1 + abs(np_random(noise * 0.5)))
            candles.append(_candle(price, close_, high_, low_))
            price = close_

        # Recuperación parcial
        rebound = scenario.crash_depth_pct * scenario.crash_recovery_pct
        rebound_per_candle = rebound / scenario.recovery_candles
        for _ in range(scenario.recovery_candles):
            factor = 1.0 + rebound_per_candle * (1 + abs(np_random(noise * 1.5)))
            close_ = price * factor
            low_ = min(price, close_) * (1 - abs(np_random(noise * 0.5)))
            high_ = max(price, close_) * (1 + abs(np_random(noise * 2)))
            candles.append(_candle(price, close_, high_, low_))
            price = close_

    elif scenario.scenario_type in (ScenarioType.GAP_DOWN, ScenarioType.GAP_UP):
        direction = -1.0 if scenario.scenario_type == ScenarioType.GAP_DOWN else 1.0
        gap_amount = scenario.base_price * scenario.gap_pct * direction

        # Gap: la última vela pre-evento cierra en base_price
        # La primera vela post-gap abre en base_price + gap
        gap_open = scenario.base_price + gap_amount
        # Gap fill inmediato (la apertura del gap)
        candles.append(_candle(
            scenario.base_price * (1 + direction * 0.001),  # small drift
            gap_open,
            max(scenario.base_price, gap_open) * 1.001,
            min(scenario.base_price, gap_open) * 0.999,
        ))
        price = gap_open

        # Velas post-gap (estabilización)
        for _ in range(scenario.post_candles):
            c = _normal(price, direction=int(-direction) if direction < 0 else 1)
            candles.append(c)
            price = c["close"]

    elif scenario.scenario_type == ScenarioType.EXTREME_VOLATILITY:
        vol_candles = 15
        base_range = scenario.base_price * 0.002  # rango normal ~0.2%
        vol_range = base_range * scenario.vol_multiplier
        for _ in range(vol_candles):
            move = np_random(vol_range)
            close_ = price + move
            # Velas con mechas enormes
            extra_wiggle = abs(np_random(vol_range * 0.5))
            if move >= 0:
                high_ = close_ + extra_wiggle
                low_ = min(price, close_) - abs(np_random(vol_range * 0.3))
            else:
                high_ = max(price, close_) + abs(np_random(vol_range * 0.3))
                low_ = close_ - extra_wiggle
            candles.append(_candle(price, close_, high_, low_))
            price = close_

    # ── Velas post-evento (estables) ──
    for _ in range(scenario.post_candles):
        c = _normal(price)
        candles.append(c)
        price = c["close"]

    return candles


# ── Calculador de slippage dinámico ────────────────────────────────

def dynamic_slippage(
    current_price: float,
    prev_price: float,
    scenario: StressScenario,
    is_stress_period: bool = True,
) -> float:
    """Calcula slippage adaptativo según la volatilidad del momento.

    En periodos de estrés retorna ``max_slippage_pct``, en periodos
    normales retorna ``normal_slippage_pct``. Adicionalmente, si el
    cambio de precio entre velas es extremo (>2× gap_pct), escala
    el slippage proporcionalmente.

    Args:
        current_price: Precio actual de mercado.
        prev_price: Precio de la vela anterior.
        scenario: Escenario activo.
        is_stress_period: Si estamos dentro del periodo de estrés.

    Returns:
        Slippage como fracción (ej: 0.05 = 5%).
    """
    if not is_stress_period:
        return scenario.normal_slippage_pct

    # Slippage base durante estrés
    slip = scenario.max_slippage_pct

    # Escalar si el movimiento entre velas es extremo
    if prev_price > 0:
        change_pct = abs(current_price - prev_price) / prev_price
        threshold = scenario.gap_pct * 2.0
        if change_pct > threshold:
            # Escalar linealmente: si cambio es 3× gap, slippage es 1.5× max
            scale = 1.0 + (change_pct / threshold - 1.0) * 0.5
            slip = min(slip * scale, 0.20)  # techo 20%

    return slip


# ── Evaluador de impacto en SL/TP ─────────────────────────────────

@dataclass
class FillEvent:
    """Resultado de evaluar si un SL o TP se ejecuta en una vela.

    Attributes:
        triggered: Si la orden se ejecutó.
        fill_price: Precio al que se ejecuta.
        reason: "sl" | "tp1" | "tp2" | "gap_sl" | "gap_tp".
        gap_through: Si la ejecución fue por un gap que saltó el nivel.
    """
    triggered: bool = False
    fill_price: float = 0.0
    reason: str = ""
    gap_through: bool = False


def check_sl_tp_in_candle(
    candle: dict[str, float],
    entry: float,
    current_sl: float,
    tp1: float,
    tp2: float,
    direction: str,
    tp1_already_hit: bool = False,
    slippage_pct: float = 0.001,
    gap_mode: bool = False,
) -> tuple[Optional[FillEvent], Optional[FillEvent]]:
    """Evalúa si SL o TP se ejecutan en una vela dada.

    Simula la lógica de Bybit: TP1 tiene prioridad sobre SL si ambos
    se tocan en la misma vela. En modo gap, si el precio abre al otro
    lado del SL/TP, se ejecuta como gap fill al peor precio.

    Args:
        candle: Vela con keys ``open``, ``high``, ``low``, ``close``.
        entry: Precio de entrada de la posición.
        current_sl: Nivel de Stop Loss actual.
        tp1: Precio de Take Profit 1.
        tp2: Precio de Take Profit 2.
        direction: "LONG" o "SHORT".
        tp1_already_hit: Si TP1 ya se activó en velas anteriores.
        slippage_pct: Slippage a aplicar en la ejecución.
        gap_mode: Si es True, usa la apertura de la vela como precio
                  de fill para gaps (price salta el nivel).

    Returns:
        Tuple de (tp_event, sl_event). None si no se ejecutó.
        TP1 se evalúa antes que SL si ambos son posibles.
    """
    h = candle["high"]
    l = candle["low"]
    o = candle["open"]
    c = candle["close"]
    sl_event: Optional[FillEvent] = None
    tp_event: Optional[FillEvent] = None
    tp1_hit_this_candle = False  # bandera para priorizar TP1 sobre SL en misma vela

    if direction == "LONG":
        # ── Gap detection: precio abre por debajo del SL ──
        if gap_mode and o < current_sl and current_sl < entry:
            fill = min(o, c) * (1.0 - slippage_pct)
            sl_event = FillEvent(triggered=True, fill_price=fill,
                                 reason="gap_sl", gap_through=True)

        # ── Gap detection: precio abre por encima de TP2 ──
        if gap_mode and not tp1_already_hit and o > tp2:
            fill = max(o, c) * (1.0 + slippage_pct)
            tp_event = FillEvent(triggered=True, fill_price=fill,
                                 reason="gap_tp", gap_through=True)
            tp1_already_hit = True
            tp1_hit_this_candle = True

        # ── TP1 (prioridad sobre SL) ──
        if not tp1_already_hit and h >= tp1:
            if h >= tp2:
                # TP1 y TP2 en misma vela
                fill = tp2 * (1.0 + slippage_pct)
                tp_event = FillEvent(triggered=True, fill_price=fill, reason="tp2")
            else:
                fill = tp1 * (1.0 + slippage_pct)
                tp_event = FillEvent(triggered=True, fill_price=fill, reason="tp1")
            tp1_already_hit = True
            tp1_hit_this_candle = True

        # ── SL (después de TP1, solo si TP1 no se tocó en esta misma vela) ──
        if not sl_event and not tp1_hit_this_candle and l <= current_sl:
            fill = current_sl * (1.0 - slippage_pct)
            sl_event = FillEvent(triggered=True, fill_price=fill, reason="sl")

    else:  # SHORT
        # ── Gap: precio abre por encima del SL ──
        if gap_mode and o > current_sl and current_sl > entry:
            fill = max(o, c) * (1.0 + slippage_pct)
            sl_event = FillEvent(triggered=True, fill_price=fill,
                                 reason="gap_sl", gap_through=True)

        # ── Gap: precio abre por debajo de TP2 ──
        if gap_mode and not tp1_already_hit and o < tp2:
            fill = min(o, c) * (1.0 - slippage_pct)
            tp_event = FillEvent(triggered=True, fill_price=fill,
                                 reason="gap_tp", gap_through=True)
            tp1_already_hit = True
            tp1_hit_this_candle = True

        # ── TP1 (prioridad sobre SL) ──
        if not tp1_already_hit and l <= tp1:
            if l <= tp2:
                fill = tp2 * (1.0 - slippage_pct)
                tp_event = FillEvent(triggered=True, fill_price=fill, reason="tp2")
            else:
                fill = tp1 * (1.0 - slippage_pct)
                tp_event = FillEvent(triggered=True, fill_price=fill, reason="tp1")
            tp1_already_hit = True
            tp1_hit_this_candle = True

        # ── SL ──
        if not sl_event and not tp1_hit_this_candle and h >= current_sl:
            fill = current_sl * (1.0 + slippage_pct)
            sl_event = FillEvent(triggered=True, fill_price=fill, reason="sl")

    return tp_event, sl_event


# ── Runner de stress test ─────────────────────────────────────────

@dataclass
class StressTestResult:
    """Resultado de ejecutar un escenario de estrés.

    Attributes:
        scenario: Escenario ejecutado.
        initial_balance: Balance inicial del paper executor.
        final_balance: Balance tras el evento de estrés.
        max_drawdown_pct: Máximo drawdown durante el evento.
        sl_triggered: Si el SL se disparó durante el evento.
        tp_triggered: Si el TP se disparó durante el evento.
        gap_through_sl: Si un gap saltó el SL sin tocarlo.
        worst_slippage_pct: Peor slippage aplicado.
        fill_events: Lista de eventos de fill ocurridos.
        candle_count: Velas totales simuladas.
        passed: True si la posición sobrevivió sin pérdida catastrófica
                (drawdown < 50% o SL funcionó correctamente).
    """
    scenario: StressScenario
    initial_balance: float = 0.0
    final_balance: float = 0.0
    max_drawdown_pct: float = 0.0
    sl_triggered: bool = False
    tp_triggered: bool = False
    gap_through_sl: bool = False
    worst_slippage_pct: float = 0.0
    fill_events: list[FillEvent] = field(default_factory=list)
    candle_count: int = 0
    passed: bool = True

    @property
    def balance_change_pct(self) -> float:
        if self.initial_balance == 0:
            return 0.0
        return (self.final_balance - self.initial_balance) / self.initial_balance * 100

    def summary(self) -> dict[str, Any]:
        return {
            "Escenario": self.scenario.label,
            "Tipo": self.scenario.scenario_type.value,
            "Balance inicial": f"${self.initial_balance:.2f}",
            "Balance final": f"${self.final_balance:.2f}",
            "Cambio": f"{self.balance_change_pct:+.2f}%",
            "Max Drawdown": f"{self.max_drawdown_pct:.2f}%",
            "SL disparado": self.sl_triggered,
            "TP disparado": self.tp_triggered,
            "Gap through SL": self.gap_through_sl,
            "Peor slippage": f"{self.worst_slippage_pct*100:.2f}%",
            "Eventos de fill": len(self.fill_events),
            "Velas simuladas": self.candle_count,
            "Estado": "✅ PASÓ" if self.passed else "❌ FALLÓ",
        }


async def _open_position_safely(
    executor: Any,
    direction: str,
    entry: float,
    sl: float,
    tp1: float,
    tp2: float,
    qty: float,
) -> bool:
    """Abre posición de forma segura, compatible con event loops existentes."""
    return await executor.open_position(direction, entry, sl, tp1, tp2, qty)


def _compute_stress_params(
    scenario: StressScenario,
    direction: str,
) -> tuple[float, float, float, float, float]:
    """Calcula entry, sl, tp1, tp2 y qty para el escenario."""
    base = scenario.base_price
    sl_dist = base * 0.02  # SL 2%
    tp_dist = base * 0.03  # TP 3%
    qty = 100.0

    if direction == "LONG":
        return base, base - sl_dist, base + tp_dist, base + tp_dist * 1.5, qty
    else:
        return base, base + sl_dist, base - tp_dist, base - tp_dist * 1.5, qty


def _calc_stress_end(scenario: StressScenario) -> int:
    """Calcula el índice de la última vela considerada como período de estrés."""
    stress_end = scenario.pre_candles + scenario.crash_candles + scenario.recovery_candles
    if scenario.scenario_type in (ScenarioType.GAP_DOWN, ScenarioType.GAP_UP):
        stress_end = scenario.pre_candles + 5
    elif scenario.scenario_type == ScenarioType.EXTREME_VOLATILITY:
        stress_end = scenario.pre_candles + 15
    return stress_end


async def run_stress_scenario_async(
    executor: Any,
    scenario: StressScenario,
    direction: str = "LONG",
) -> StressTestResult:
    """Versión async de ``run_stress_scenario`` para usar dentro de event loops.

    Idéntica a la versión síncrona pero usa ``await`` para abrir la posición,
    evitando bloqueos del event loop.
    """
    logger.info(f"🧪 Ejecutando stress test (async): {scenario.label} ({direction})")
    entry, sl, tp1, tp2, qty = _compute_stress_params(scenario, direction)
    opened = await _open_position_safely(executor, direction, entry, sl, tp1, tp2, qty)
    return _execute_stress_candles(executor, scenario, direction, entry, opened, qty)


def run_stress_scenario(
    executor: Any,
    scenario: StressScenario,
    direction: str = "LONG",
) -> StressTestResult:
    """Ejecuta un escenario de estrés sobre un PaperExecutor.

    1. Abre una posición con el executor.
    2. Alimenta velas generadas por ``generate_stress_candles``.
    3. En cada vela evalúa SL/TP con slippage dinámico.
    4. Cierra la posición si SL o TP se disparan.
    5. Reporta métricas del impacto.

    Args:
        executor: Instancia de PaperExecutor con posición cerrable.
        scenario: Escenario de estrés a simular.
        direction: Dirección de la posición a abrir ("LONG" | "SHORT").

    Returns:
        StressTestResult con métricas del evento.
    """
    logger.info(f"🧪 Ejecutando stress test: {scenario.label} ({direction})")
    entry, sl, tp1, tp2, qty = _compute_stress_params(scenario, direction)

    # Ejecutar open_position en el event loop correspondiente
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            # Ya hay un loop corriendo en este hilo → run_until_complete
            # (en un loop diferente, run_coroutine_threadsafe + result())
            import threading
            if threading.current_thread() is threading.main_thread():
                # Mismo hilo: schedule como tarea y esperar con Event
                done = threading.Event()
                result_holder: list[bool] = [False]

                async def _open_and_signal() -> None:
                    result_holder[0] = await executor.open_position(
                        direction, entry, sl, tp1, tp2, qty,
                    )
                    done.set()

                loop.call_soon_threadsafe(lambda: loop.create_task(_open_and_signal()))
                done.wait(timeout=30.0)
                opened = result_holder[0]
            else:
                # Hilo diferente: usar run_coroutine_threadsafe
                future = asyncio.run_coroutine_threadsafe(
                    executor.open_position(direction, entry, sl, tp1, tp2, qty),
                    loop,
                )
                opened = future.result(timeout=30)
        else:
            opened = loop.run_until_complete(
                executor.open_position(direction, entry, sl, tp1, tp2, qty)
            )
    except RuntimeError:
        opened = asyncio.run(
            executor.open_position(direction, entry, sl, tp1, tp2, qty)
        )

    # ── Delegar a la lógica común de simulación de velas ──
    return _execute_stress_candles(executor, scenario, direction, entry, opened, qty)


def _execute_stress_candles(
    executor: Any,
    scenario: StressScenario,
    direction: str,
    base: float,
    opened: bool,
    qty: float,
) -> StressTestResult:
    """Ejecuta la simulación de velas sobre una posición ya abierta.

    Compartido entre las versiones sync y async de run_stress_scenario.
    """
    if not opened:
        logger.warning(f"No se pudo abrir posición para {scenario.label}")
        return StressTestResult(
            scenario=scenario,
            initial_balance=executor.paper_balance,
            final_balance=executor.paper_balance,
            passed=False,
        )

    initial_balance = executor.paper_balance + (qty * executor.entry_price * 0.5)
    peak_balance = executor.paper_balance
    trough_balance = executor.paper_balance
    max_dd = 0.0
    worst_slip = 0.0
    fill_events: list[FillEvent] = []
    sl_triggered = False
    tp_triggered = False
    gap_through_sl = False
    prev_price = base

    # ── Generar velas del escenario ──
    candles = generate_stress_candles(scenario)

    # ── Estado interno de la posición ──
    tp1_hit = False
    current_sl = executor.current_sl
    stress_end = _calc_stress_end(scenario)

    for i, candle in enumerate(candles):
        if not executor.position_open:
            break

        is_stress = i <= stress_end
        slip = dynamic_slippage(candle["close"], prev_price, scenario, is_stress)
        worst_slip = max(worst_slip, slip)

        gap_mode = scenario.scenario_type in (ScenarioType.GAP_DOWN, ScenarioType.GAP_UP) and is_stress

        tp_ev, sl_ev = check_sl_tp_in_candle(
            candle=candle,
            entry=executor.entry_price,
            current_sl=current_sl,
            tp1=executor.tp1_price,
            tp2=executor.tp2_price,
            direction=direction,
            tp1_already_hit=tp1_hit,
            slippage_pct=slip,
            gap_mode=gap_mode,
        )

        if tp_ev and tp_ev.triggered:
            tp_triggered = True
            fill_events.append(tp_ev)
            if tp_ev.reason == "tp1":
                tp1_hit = True
                current_sl = executor.entry_price
                logger.info(f"  📈 TP1 hit @ {tp_ev.fill_price:.4f} (gap={tp_ev.gap_through})")
                executor.tp1_hit = True
                executor.current_sl = executor.entry_price
            elif tp_ev.reason == "tp2":
                executor.last_price = tp_ev.fill_price
                executor.close_position()
                fill_events.append(sl_ev) if sl_ev else None
                logger.info(f"  📈 TP2 hit @ {tp_ev.fill_price:.4f} — posición cerrada")
                break

        if sl_ev and sl_ev.triggered:
            sl_triggered = True
            gap_through_sl = sl_ev.gap_through
            fill_events.append(sl_ev)
            executor.last_price = sl_ev.fill_price
            executor.close_position()
            logger.info(f"  📉 SL hit @ {sl_ev.fill_price:.4f} (gap={sl_ev.gap_through}) — posición cerrada")
            break

        executor.last_price = candle["close"]
        current_balance = executor.paper_balance
        peak_balance = max(peak_balance, current_balance)
        trough_balance = min(trough_balance, current_balance)
        if peak_balance > 0:
            dd = (peak_balance - trough_balance) / peak_balance * 100
            max_dd = max(max_dd, dd)

        prev_price = candle["close"]

    # ── Si la posición sigue abierta tras el escenario, cerrar ──
    if executor.position_open:
        executor.last_price = candles[-1]["close"]
        executor.close_position()
        logger.info(f"  🏁 Posición cerrada al final del escenario @ {candles[-1]['close']:.4f}")

    final_balance = executor.paper_balance
    passed = not (max_dd > 50.0 and not sl_triggered)

    logger.info(
        f"📊 Resultado {scenario.label}: "
        f"Balance ${final_balance:.2f} | "
        f"Max DD {max_dd:.2f}% | "
        f"SL={sl_triggered} TP={tp_triggered} | "
        f"{'✅ PASÓ' if passed else '❌ FALLÓ'}"
    )

    return StressTestResult(
        scenario=scenario,
        initial_balance=initial_balance,
        final_balance=final_balance,
        max_drawdown_pct=round(max_dd, 2),
        sl_triggered=sl_triggered,
        tp_triggered=tp_triggered,
        gap_through_sl=gap_through_sl,
        worst_slippage_pct=worst_slip,
        fill_events=fill_events,
        candle_count=len(candles),
        passed=passed,
    )


# ── Suite completa de stress tests ─────────────────────────────────

async def run_all_stress_scenarios_async(
    executor: Any,
    base_price: float = 0.50,
    scenarios: Optional[list[StressScenario]] = None,
) -> list[StressTestResult]:
    """Versión async de ``run_all_stress_scenarios`` para usar dentro de event loops."""
    if scenarios is None:
        scenarios = [
            FLASH_CRASH_SEVERE,
            FLASH_CRASH_MODERATE,
            GAP_DOWN_5PCT,
            GAP_DOWN_10PCT,
            GAP_UP_5PCT,
            VOLATILITY_EXTREME,
        ]

    results: list[StressTestResult] = []
    for sc in scenarios:
        sc.base_price = base_price
        for direction in ("LONG", "SHORT"):
            import copy
            fresh = copy.copy(executor)
            fresh.paper_balance = executor.initial_balance
            fresh.position_open = False
            fresh.total_trades = 0
            fresh.wins = 0
            fresh.losses = 0
            fresh.total_pnl = 0.0
            fresh.total_commission = 0.0

            entry, sl, tp1, tp2, qty = _compute_stress_params(sc, direction)
            opened = await fresh.open_position(direction, entry, sl, tp1, tp2, qty)
            result = _execute_stress_candles(fresh, sc, direction, entry, opened, qty)
            results.append(result)

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    logger.info(
        f"\n{'='*50}\n"
        f"🏁 STRESS TEST SUITE COMPLETADA (async)\n"
        f"  Total: {len(results)} | ✅ Pasaron: {passed} | ❌ Fallaron: {failed}\n"
        f"{'='*50}"
    )
    return results


def run_all_stress_scenarios(
    executor: Any,
    base_price: float = 0.50,
    scenarios: Optional[list[StressScenario]] = None,
) -> list[StressTestResult]:
    """Ejecuta todos los escenarios de estrés contra un executor.

    Para cada escenario, prueba tanto LONG como SHORT.
    Versión síncrona que usa ``asyncio.run()`` internamente.

    Args:
        executor: PaperExecutor a estresar.
        base_price: Precio base para los escenarios.
        scenarios: Lista de escenarios a ejecutar. Por defecto todos.

    Returns:
        Lista de resultados.
    """
    return asyncio.run(
        run_all_stress_scenarios_async(executor, base_price, scenarios),
    )
