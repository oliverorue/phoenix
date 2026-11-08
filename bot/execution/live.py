"""Ejecución live (mainnet/testnet): órdenes reales + reconciliación del ciclo.

Modelo **exchange-managed**: al abrir, el bot coloca entrada a mercado + SL
(``closePosition``) + TP1 (parcial) + TP2 (resto). El exchange dispara las
salidas. El bot reconcilia periódicamente:

- Si el TP1 parcial se llenó → mueve el SL a breakeven.
- Si la posición se cerró → finaliza el trade con el PnL de los fills.

Todas las salidas son ``reduceOnly``/``closePosition``. Este módulo NO decide
cuándo abrir (eso lo hace el bridge); solo ejecuta y reconcilia.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from bot.execution.broker import LiveBroker
from bot.execution.models import Trade


@dataclass
class LiveState:
    """Estado de un trade live en el exchange."""

    trade_id: str
    symbol: str
    direction: str
    qty: float
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    tp1_qty: float
    tp2_qty: float
    opened_ms: int
    sl_order_id: Optional[int] = None
    tp1_order_id: Optional[int] = None
    tp2_order_id: Optional[int] = None
    breakeven_moved: bool = False
    closed: bool = False
    realized_pnl: float = 0.0
    exit_price: float = 0.0


class LiveTradeExecutor:
    """Ejecuta y reconcilia un trade live sobre el exchange."""

    def __init__(self, broker: LiveBroker) -> None:
        self._broker = broker

    async def open(self, trade: Trade, leverage: int = 1) -> LiveState:
        """Abre a mercado y coloca SL + TP1 parcial + TP2."""
        direction = trade.direction.value
        tp1_qty = trade.qty * trade.tp1_ratio
        tp2_qty = trade.qty - tp1_qty
        state = LiveState(
            trade_id=trade.id, symbol=trade.symbol, direction=direction,
            qty=trade.qty, entry_price=trade.entry_price,
            sl_price=trade.sl_price, tp1_price=trade.tp1_price,
            tp2_price=trade.tp2_price, tp1_qty=tp1_qty, tp2_qty=tp2_qty,
            opened_ms=int(time.time() * 1000),
        )
        await self._broker.set_margin(trade.symbol, leverage)
        await self._broker.open_market(trade.symbol, direction, trade.qty)

        sl = await self._broker.set_sl(trade.symbol, direction, trade.sl_price)
        state.sl_order_id = sl.get("orderId")
        tp1 = await self._broker.set_tp(
            trade.symbol, direction, trade.tp1_price, qty=tp1_qty,
        )
        state.tp1_order_id = tp1.get("orderId")
        tp2 = await self._broker.set_tp(
            trade.symbol, direction, trade.tp2_price, qty=tp2_qty,
        )
        state.tp2_order_id = tp2.get("orderId")
        return state

    async def reconcile(self, state: LiveState) -> bool:
        """Avanza el ciclo contra el exchange. Devuelve True si el trade cerró."""
        if state.closed:
            return True

        # 1. TP1 parcial lleno → SL a breakeven (una sola vez).
        if not state.breakeven_moved and state.tp1_order_id is not None:
            order = await self._broker.order(state.symbol, str(state.tp1_order_id))
            if order.get("status") == "FILLED":
                await self._broker.move_sl(
                    state.symbol, state.direction, state.entry_price,
                )
                state.breakeven_moved = True

        # 2. Posición cerrada → finalizar con PnL de los fills del trade.
        position = await self._broker.position(state.symbol)
        size = float(position.get("size", 0.0)) if position else 0.0
        if size == 0.0:
            fills = await self._broker.fills(state.symbol)
            relevant = [
                f for f in fills if int(f.get("time") or 0) >= state.opened_ms
            ]
            state.realized_pnl = sum(f["realized_pnl"] for f in relevant)
            if relevant:
                state.exit_price = relevant[-1]["price"]
            state.closed = True
            return True
        return False
