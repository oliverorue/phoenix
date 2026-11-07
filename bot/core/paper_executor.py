"""
Paper Trading Executor — Simula ejecución de órdenes sin llamadas reales a la API.

Misma interfaz que OrderExecutor para ser inyectado en SymbolRunner vía DI.
Usa precios reales del DataFeed para simular fills con slippage configurable.
"""

from __future__ import annotations

import math
import time
from typing import Any, Optional
from loguru import logger
from bot.core.data_feed import DataFeed, decimal_places_for_qty_step


class PaperExecutor:
    """Simula la ejecución de órdenes de trading sin llamar a la API real.

    Mantiene un balance virtual (``paper_balance``) y calcula P&L realista
    usando el último precio de mercado con slippage configurable.

    Args:
        data_feed: DataFeed para obtener precios e info del instrumento.
        journal: TradeJournal opcional para registrar operaciones.
        initial_balance: Balance virtual inicial en USDT.
        slippage_pct: Slippage aplicado al entry/exit (ej: 0.001 = 0.1%).
        commission_pct: Comisión taker simulada (market/SL) en porcentaje.
        maker_commission_pct: Comisión maker simulada (TP limit) en porcentaje.
    """

    def __init__(
        self,
        data_feed: DataFeed,
        journal: Any = None,
        initial_balance: float = 10000.0,
        slippage_pct: float = 0.001,
        commission_pct: float = 0.0005,
        maker_commission_pct: float = 0.0001,
    ) -> None:
        self.data = data_feed
        self.journal = journal
        self.paper_balance = initial_balance
        self.initial_balance = initial_balance
        self.slippage_pct = slippage_pct
        self.commission_pct = commission_pct
        self.maker_commission_pct = maker_commission_pct
        self.leverage = 1

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

        # Cantidades y PnL del trade actual.
        self._entry_qty: float = 0.0
        self._tp1_qty: float = 0.0
        self._tp2_qty: float = 0.0
        self._realized_net: float = 0.0
        self._realized_gross: float = 0.0

        # ── Estadísticas del paper trading ──
        self.total_trades: int = 0
        self.wins: int = 0
        self.losses: int = 0
        self.total_pnl: float = 0.0
        self.total_commission: float = 0.0
        self.equity_history: list[tuple[float, float]] = []
        self.last_equity: float = initial_balance

        # Cache de qtyStep para _truncate_qty
        self._qty_step: float = 0.001
        self._init_qty_step()

        # Balance y resultado del último cierre (para contabilidad de riesgo).
        self.balance_scope = "allocated"
        self._pending_close_reason = "paper_close"
        self.last_close_pnl: Optional[float] = None
        self.last_close_reason: str = ""
        self.last_close_direction: Optional[str] = None
        self.unreported_close: bool = False

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
        """Simula ajuste de apalancamiento."""
        self.leverage = max(1, int(leverage))
        logger.info(f"[PAPER] Apalancamiento configurado: {self.leverage}x (simulado)")

    async def get_balance(self) -> float:
        """Balance virtual actual."""
        return self.paper_balance

    def mark_close_reason(self, reason: str) -> None:
        """Marca la razón del próximo cierre."""
        self._pending_close_reason = reason

    def consume_close_result(self) -> Optional[dict[str, Any]]:
        """Devuelve y limpia el resultado del último cierre no reportado."""
        if not self.unreported_close:
            return None
        self.unreported_close = False
        return {
            "pnl": self.last_close_pnl,
            "reason": self.last_close_reason,
            "direction": self.last_close_direction,
        }

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
        """Abre una posición simulada con slippage y comisión de entrada."""
        if self.position_open:
            logger.warning("[PAPER] Ya hay una posición abierta, cerrando antes...")
            self.close_position(reason="replace_position")

        if qty_tp1 is None:
            qty_tp1 = qty
        if qty_tp2 is None:
            qty_tp2 = qty

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
        fill_price = max(entry * (1.0 + side_mult * self.slippage_pct), 0.0001)

        # ── Verificar margen disponible ──
        margin_required = qty * fill_price / self.leverage
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
        self._entry_qty = qty
        self._tp1_qty = qty_tp1
        self._tp2_qty = qty_tp2
        self._realized_net = 0.0
        self._realized_gross = 0.0
        self.tp1_hit = False
        self.trailing_activated = False

        # ── Comisión de entrada (market = taker) ──
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
            self.journal._open_trade["initial_qty"] = qty
            self.journal._open_trade["realized_pnl_gross"] = 0.0

        self.total_trades += 1
        self._record_equity(fill_price)
        return True

    def update_position(self, current_price: float) -> Optional[str]:
        """Simula SL/TP1/TP2 con el precio actual.

        Se llama en cada ciclo mientras la posición está abierta.
        Retorna ``"stop_loss"``, ``"tp1"``, ``"tp2"`` o ``None``.
        """
        if not self.position_open:
            return None
        if current_price <= 0:
            return None

        self.last_price = current_price
        side = self.position_type

        # ── SL primero (criterio conservador) ──
        if self.current_sl > 0:
            hit_sl = (
                current_price <= self.current_sl
                if side == "LONG"
                else current_price >= self.current_sl
            )
            if hit_sl:
                slip = -1.0 if side == "LONG" else 1.0
                fill = max(self.current_sl * (1.0 + slip * self.slippage_pct), 0.0001)
                self._apply_fill(self.quantity, fill, maker=False)
                self._finish_close(fill, "stop_loss")
                return "stop_loss"

        # ── TP1 parcial ──
        if not self.tp1_hit and self.tp1_price > 0:
            hit_tp1 = (
                current_price >= self.tp1_price
                if side == "LONG"
                else current_price <= self.tp1_price
            )
            if hit_tp1:
                qty = min(self._tp1_qty or self.quantity, self.quantity)
                self._apply_fill(qty, self.tp1_price, maker=True)
                self.tp1_hit = True
                if self.journal and self.journal._open_trade:
                    self.journal._open_trade["tp1_hit"] = True
                logger.info(
                    f"[PAPER] 🎯 TP1 ejecutado {qty} @ {self.tp1_price:.4f} | "
                    f"Remanente: {self.quantity}"
                )
                if self.quantity <= 0:
                    self._finish_close(self.tp1_price, "tp1")
                    return "tp1"
                return "tp1"

        # ── TP2 (solo después de TP1) ──
        if self.tp1_hit and self.tp2_price > 0 and self.quantity > 0:
            hit_tp2 = (
                current_price >= self.tp2_price
                if side == "LONG"
                else current_price <= self.tp2_price
            )
            if hit_tp2:
                self._apply_fill(self.quantity, self.tp2_price, maker=True)
                if self.journal and self.journal._open_trade:
                    self.journal._open_trade["tp2_hit"] = True
                self._finish_close(self.tp2_price, "tp2")
                return "tp2"

        self._record_equity(current_price)
        return None

    def close_position(self, reason: Optional[str] = None) -> None:
        """Cierra la posición simulada a mercado con slippage taker."""
        if not self.position_open:
            return
        if reason is not None:
            self._pending_close_reason = reason
        reason = self._pending_close_reason

        side = self.position_type
        exit_price = self.last_price if self.last_price > 0 else self.entry_price
        slip = -1.0 if side == "LONG" else 1.0
        fill_exit = max(exit_price * (1.0 + slip * self.slippage_pct), 0.0001)

        self._apply_fill(self.quantity, fill_exit, maker=False)
        self._finish_close(fill_exit, reason)

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

    def update_equity(self, current_price: Optional[float] = None) -> float:
        """Calcula equity (balance + PnL no realizado) y la registra."""
        return self._record_equity(current_price)

    # ── Helpers internos ─────────────────────────────────────────────

    def _record_equity(self, current_price: Optional[float] = None) -> float:
        if current_price is None:
            current_price = self.last_price
        unrealized = 0.0
        if self.position_open and current_price > 0 and self.entry_price > 0:
            if self.position_type == "LONG":
                unrealized = (current_price - self.entry_price) * self.quantity
            else:
                unrealized = (self.entry_price - current_price) * self.quantity
        equity = self.paper_balance + unrealized
        self.last_equity = equity
        self.equity_history.append((time.time(), equity))
        if len(self.equity_history) > 10000:
            self.equity_history = self.equity_history[-10000:]
        return equity

    def _apply_fill(self, qty: float, price: float, maker: bool) -> float:
        """Aplica un fill parcial/total y actualiza balance, stats y journal."""
        if qty <= 0 or not self.position_open:
            return 0.0
        qty = min(qty, self.quantity)
        if qty <= 0:
            return 0.0

        if self.position_type == "LONG":
            pnl_gross = (price - self.entry_price) * qty
        else:
            pnl_gross = (self.entry_price - price) * qty

        rate = self.maker_commission_pct if maker else self.commission_pct
        commission = qty * price * rate
        pnl_net = pnl_gross - commission

        self.paper_balance += pnl_net
        self.total_commission += commission
        self.total_pnl += pnl_net
        self._realized_net += pnl_net
        self._realized_gross += pnl_gross
        self.quantity = round(max(self.quantity - qty, 0.0), 12)

        if self.journal and self.journal._open_trade:
            self.journal._open_trade["qty"] = self.quantity
            self.journal._open_trade["realized_pnl_gross"] = self._realized_gross
            self.journal._open_trade["commission_usdt"] = (
                float(self.journal._open_trade.get("commission_usdt", 0.0))
                + commission
            )

        self._record_equity(price)
        return pnl_net

    def _finish_close(self, exit_price: float, reason: str) -> None:
        """Cierra el estado del trade actual."""
        if not self.position_open:
            return

        direction = self.position_type
        total_pnl = self._realized_net

        if self.journal:
            self.journal.close(
                exit_price=exit_price,
                close_reason=reason,
                sl_final=self.current_sl,
            )

        if total_pnl > 0:
            self.wins += 1
        elif total_pnl < 0:
            self.losses += 1

        logger.info(
            f"[PAPER] Posición {direction} cerrada | "
            f"Exit: {exit_price:.4f} | PnL neto: {total_pnl:+.4f} USDT | "
            f"Balance: ${self.paper_balance:.2f}"
        )

        self.last_close_pnl = total_pnl
        self.last_close_reason = reason
        self.last_close_direction = direction
        self.unreported_close = True
        self._pending_close_reason = "paper_close"

        # ── Resetear estado ──
        self.position_open = False
        self.position_type = None
        self.entry_price = 0.0
        self.quantity = 0.0
        self.current_sl = 0.0
        self._entry_qty = 0.0
        self._tp1_qty = 0.0
        self._tp2_qty = 0.0
        self._realized_net = 0.0
        self._realized_gross = 0.0
        self.tp1_hit = False
        self.trailing_activated = False
        self._record_equity(exit_price)

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
        unrealized = 0.0
        if self.position_open and self.last_price > 0 and self.entry_price > 0:
            if self.position_type == "LONG":
                unrealized = (self.last_price - self.entry_price) * self.quantity
            else:
                unrealized = (self.entry_price - self.last_price) * self.quantity
        equity = self.paper_balance + unrealized
        return {
            "initial_balance": self.initial_balance,
            "current_balance": round(self.paper_balance, 2),
            "current_equity": round(equity, 2),
            "unrealized_pnl": round(unrealized, 4),
            "return_pct": round(
                (equity - self.initial_balance) / self.initial_balance * 100, 2
            ) if self.initial_balance else 0.0,
            "total_pnl": round(self.total_pnl, 4),
            "total_commission": round(self.total_commission, 4),
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.wins / max(self.total_trades, 1) * 100, 2),
            "position_open": self.position_open,
            "position_type": self.position_type,
        }
