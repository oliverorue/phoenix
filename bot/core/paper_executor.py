"""
Paper Trading Executor — Simula ejecución de órdenes sin llamadas reales a la API.

Misma interfaz que OrderExecutor para ser inyectado en SymbolRunner vía DI.
Usa precios reales del DataFeed para simular fills con slippage configurable.
"""

from __future__ import annotations

import math
from typing import Any, Optional
from loguru import logger
from bot.core.data_feed import DataFeed, decimal_places_for_qty_step


class PaperExecutor:
    """Simula la ejecución de órdenes de trading sin llamar a la API de Bybit.

    Mantiene un balance virtual (``paper_balance``) y calcula P&L realista
    usando el último precio de mercado con slippage configurable.

    Args:
        data_feed: DataFeed para obtener precios e info del instrumento.
        journal: TradeJournal opcional para registrar operaciones.
        initial_balance: Balance virtual inicial en USDT.
        slippage_pct: Slippage aplicado al entry/exit (ej: 0.001 = 0.1%).
        commission_pct: Comisión simulada (maker/taker) en porcentaje.
    """

    def __init__(
        self,
        data_feed: DataFeed,
        journal: Any = None,
        initial_balance: float = 10000.0,
        slippage_pct: float = 0.001,
        commission_pct: float = 0.0005,  # 0.05% equivalente a Bybit taker
    ) -> None:
        self.data = data_feed
        self.journal = journal
        self.paper_balance = initial_balance
        self.initial_balance = initial_balance
        self.slippage_pct = slippage_pct
        self.commission_pct = commission_pct

        # ── Estado de la posición (mismo interfaz que OrderExecutor) ──
        self.position_open = False
        self.position_type: Optional[str] = None  # LONG / SHORT
        self.entry_price: float = 0.0
        self.quantity: float = 0.0
        self.current_sl: float = 0.0
        self.tp1_price: float = 0.0
        self.tp2_price: float = 0.0
        self.tp1_hit: bool = False
        self.trailing_activated = False
        self.last_price: float = 0.0

        # ── Estadísticas del paper trading ──
        self.total_trades: int = 0
        self.wins: int = 0
        self.losses: int = 0
        self.total_pnl: float = 0.0
        self.total_commission: float = 0.0

        # Cache de qtyStep para _truncate_qty
        self._qty_step: float = 0.001
        self._init_qty_step()

    def _init_qty_step(self) -> None:
        """Intenta obtener qtyStep del caché de DataFeed o usa fallback."""
        try:
            if hasattr(self.data, '_instrument_cache') and self.data._instrument_cache:
                self._qty_step = float(self.data._instrument_cache['qtyStep'])
            else:
                self._qty_step = 0.001
        except Exception:
            self._qty_step = 0.001

    # ── Interfaz pública (misma que OrderExecutor) ──────────────────

    async def set_leverage(self, leverage: int) -> None:
        """Simula ajuste de apalancamiento (solo log)."""
        logger.info(f"[PAPER] Apalancamiento configurado: {leverage}x (simulado)")

    async def open_position(
        self,
        direction: str,
        entry: float,
        sl: float,
        tp1: float,
        tp2: float,
        qty: float,
        qty_tp1: Optional[float] = None,
        qty_tp2: Optional[float] = None,
    ) -> bool:
        """Abre una posición simulada.

        Aplica slippage al entry y registra la operación en el journal.
        No hace ninguna llamada a la API real.
        """
        if self.position_open:
            logger.warning("[PAPER] Ya hay una posición abierta, cerrando antes...")
            self.close_position()

        if qty_tp1 is None:
            qty_tp1 = qty
        if qty_tp2 is None:
            qty_tp2 = qty

        # Truncar cantidades al qtyStep
        qty = self._truncate_qty(qty)
        qty_tp1 = self._truncate_qty(qty_tp1)
        qty_tp2 = self._truncate_qty(qty_tp2)

        if qty <= 0:
            logger.error(f"[PAPER] Cantidad inválida tras truncar a step={self._qty_step}: {qty}")
            return False
        if entry <= 0 or sl <= 0:
            logger.error(f"[PAPER] Precios inválidos: entry={entry}, sl={sl}")
            return False

        # ── Simular slippage en el entry ──
        side_mult = 1.0 if direction == "LONG" else -1.0
        fill_price = entry * (1.0 + side_mult * self.slippage_pct)
        fill_price = max(fill_price, 0.0001)

        # ── Verificar que hay balance suficiente ──
        margin_required = qty * fill_price / 1.0  # sin apalancamiento real
        if margin_required > self.paper_balance:
            logger.warning(
                f"[PAPER] Balance insuficiente: ${self.paper_balance:.2f} < "
                f"${margin_required:.2f} requeridos"
            )
            return False

        # ── Registrar estado ──
        self.position_open = True
        self.position_type = direction
        self.entry_price = fill_price
        self.current_sl = sl
        self.tp1_price = tp1
        self.tp2_price = tp2
        self.quantity = qty
        self.tp1_hit = False
        self.trailing_activated = False

        # ── Comisión de entrada ──
        commission_entry = qty * fill_price * self.commission_pct
        self.paper_balance -= commission_entry
        self.total_commission += commission_entry

        logger.info(
            f"[PAPER] ✅ {direction} abierto | Entry simulado: {fill_price:.4f} "
            f"(slippage {self.slippage_pct*100:.2f}%) | "
            f"SL: {sl:.4f} | Qty: {qty} | Balance: ${self.paper_balance:.2f}"
        )

        # ── Registrar en journal ──
        if self.journal:
            self.journal.open(
                symbol=self.data.symbol,
                side=direction,
                entry_price=fill_price,
                qty=qty,
                sl=sl,
                tp1=tp1,
                tp2=tp2,
            )
        if self.journal and self.journal._open_trade:
            self.journal._open_trade["commission_usdt"] = commission_entry

        self.total_trades += 1
        return True

    def close_position(self) -> None:
        """Cierra la posición simulada.

        Usa ``last_price`` (actualizado desde run_cycle) como precio de salida
        con slippage. Calcula P&L, actualiza balance paper y registra en journal.
        """
        if not self.position_open:
            return

        direction = self.position_type
        entry = self.entry_price
        qty = self.quantity
        exit_price = self.last_price if self.last_price > 0 else self.entry_price

        # ── Simular slippage en la salida ──
        side_mult = -1.0 if direction == "LONG" else 1.0
        fill_exit = exit_price * (1.0 + side_mult * self.slippage_pct)
        fill_exit = max(fill_exit, 0.0001)

        # ── Calcular P&L ──
        if direction == "LONG":
            pnl_gross = (fill_exit - entry) * qty
        else:
            pnl_gross = (entry - fill_exit) * qty

        # ── Comisión de salida ──
        commission_exit = qty * fill_exit * self.commission_pct
        pnl_net = pnl_gross - commission_exit

        self.paper_balance += pnl_net
        self.total_commission += commission_exit
        self.total_pnl += pnl_net

        if pnl_net > 0:
            self.wins += 1
        elif pnl_net < 0:
            self.losses += 1

        logger.info(
            f"[PAPER] Posición {direction} cerrada | "
            f"Exit simulado: {fill_exit:.4f} | "
            f"PnL: {pnl_net:+.4f} USDT ({pnl_gross:+.4f} - {commission_exit:.4f} com.) | "
            f"Balance: ${self.paper_balance:.2f}"
        )

        # ── Registrar en journal ──
        if self.journal:
            self.journal.close(
                exit_price=fill_exit,
                close_reason="paper_close",
                sl_final=self.current_sl,
            )

        # ── Resetear estado ──
        self.position_open = False
        self.position_type = None
        self.entry_price = 0.0
        self.quantity = 0.0
        self.current_sl = 0.0
        self.tp1_hit = False
        self.trailing_activated = False

    def update_stop_loss(self, new_sl: float) -> None:
        """Actualiza el Stop Loss simulado."""
        self.current_sl = new_sl
        logger.info(f"[PAPER] SL actualizado a {new_sl:.4f} (simulado)")

    def activate_trailing_stop(self, distance: float, activation_price: float) -> bool:
        """Simula activación de trailing stop."""
        self.trailing_activated = True
        logger.info(
            f"[PAPER] 🎯 Trailing stop activado | "
            f"Dist: {distance:.4f} | Activación: {activation_price:.4f} (simulado)"
        )
        return True

    # ── Helpers ─────────────────────────────────────────────────────

    def _truncate_qty(self, qty: float) -> float:
        """Trunca cantidad al qtyStep del instrumento (misma lógica que OrderExecutor)."""
        step = self._qty_step
        if step > 0:
            truncated = math.floor(qty / step) * step
            decimals = decimal_places_for_qty_step(step)
            return round(truncated, decimals)
        return qty

    # ── Propiedades de consulta ──

    @property
    def stats(self) -> dict[str, Any]:
        """Estadísticas acumuladas del paper trading."""
        return {
            "initial_balance": self.initial_balance,
            "current_balance": round(self.paper_balance, 2),
            "total_pnl": round(self.total_pnl, 4),
            "total_commission": round(self.total_commission, 4),
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.wins / max(self.total_trades, 1) * 100, 2),
            "position_open": self.position_open,
            "position_type": self.position_type,
        }
