"""State machine pura del ciclo de un trade.

Sin I/O: ``advance`` recibe el precio actual, muta el ``Trade`` y devuelve las
acciones que ocurrieron. El ``TradeManager`` se encarga de ejecutar esas
acciones contra el broker (dry-run o live) y de registrar los fills.

Si ``fee_pct > 0`` (paper), se descuenta la comisión de salida en cada cierre.
La comisión de entrada la aplica ``TradeManager.open_trade``.
"""

from __future__ import annotations

from bot.execution.models import Action, Direction, Trade, TradeStatus


def direction_sign(direction: Direction) -> float:
    return 1.0 if direction == Direction.LONG else -1.0


def _exit_fee(qty: float, price: float, fee_pct: float) -> float:
    return qty * price * fee_pct if fee_pct > 0 else 0.0


def _close(trade: Trade, price: float, reason: str, ts: float) -> None:
    """Cierra el remanente del trade al precio dado."""
    trade.exit_price = price
    trade.exit_reason = reason
    trade.closed_at = ts
    trade.remaining_qty = 0.0
    trade.status = TradeStatus.CLOSED


def advance(
    trade: Trade, price: float, ts: float = 0.0, max_hold_seconds: float = 0.0,
    fee_pct: float = 0.0,
) -> list[Action]:
    """Avanza la state machine del trade con el nuevo ``price``.

    Devuelve las acciones disparadas (PARTIAL_CLOSE, FULL_CLOSE, MOVE_SL,
    TRAIL). El trade queda mutado con su nuevo estado y PnL realizado.

    Si ``max_hold_seconds > 0`` y el trade lleva más tiempo abierto que ese
    límite (y no cerró por SL/TP en este tick), se cierra a precio de mercado
    con razón ``TIMEOUT``.
    """
    if trade.status == TradeStatus.CLOSED:
        return []

    actions: list[Action] = []
    is_long = trade.direction == Direction.LONG
    sign = direction_sign(trade.direction)

    if trade.status == TradeStatus.OPEN:
        sl_hit = price <= trade.sl_price if is_long else price >= trade.sl_price
        tp1_hit = price >= trade.tp1_price if is_long else price <= trade.tp1_price

        if sl_hit:
            actions.append(Action("FULL_CLOSE", trade.sl_price, trade.qty, "SL"))
            trade.realized_pnl += (
                (trade.sl_price - trade.entry_price) * trade.qty * sign
                - _exit_fee(trade.qty, trade.sl_price, fee_pct)
            )
            _close(trade, trade.sl_price, "SL", ts)
            return actions

        if tp1_hit:
            tp1_qty = trade.qty * trade.tp1_ratio
            actions.append(Action("PARTIAL_CLOSE", trade.tp1_price, tp1_qty, "TP1"))
            trade.realized_pnl += (
                (trade.tp1_price - trade.entry_price) * tp1_qty * sign
                - _exit_fee(tp1_qty, trade.tp1_price, fee_pct)
            )
            trade.remaining_qty = trade.qty - tp1_qty
            trade.sl_price = trade.entry_price  # breakeven
            trade.status = TradeStatus.PARTIAL
            actions.append(Action("MOVE_SL", trade.entry_price, 0.0, "BREAKEVEN"))
    elif trade.status == TradeStatus.PARTIAL:
        # Trailing: el SL se arrastra a favor del precio, nunca en contra.
        if trade.trailing_dist > 0:
            if is_long:
                new_sl = max(trade.sl_price, price - trade.trailing_dist)
            else:
                new_sl = min(trade.sl_price, price + trade.trailing_dist)
            if new_sl != trade.sl_price:
                trade.sl_price = new_sl
                actions.append(Action("TRAIL", new_sl, 0.0, "TRAILING"))

        sl_hit = price <= trade.sl_price if is_long else price >= trade.sl_price
        tp2_hit = price >= trade.tp2_price if is_long else price <= trade.tp2_price

        if sl_hit:
            actions.append(
                Action("FULL_CLOSE", trade.sl_price, trade.remaining_qty, "TRAILING_SL")
            )
            trade.realized_pnl += (
                (trade.sl_price - trade.entry_price) * trade.remaining_qty * sign
                - _exit_fee(trade.remaining_qty, trade.sl_price, fee_pct)
            )
            _close(trade, trade.sl_price, "TRAILING_SL", ts)
        elif tp2_hit:
            actions.append(
                Action("FULL_CLOSE", trade.tp2_price, trade.remaining_qty, "TP2")
            )
            trade.realized_pnl += (
                (trade.tp2_price - trade.entry_price) * trade.remaining_qty * sign
                - _exit_fee(trade.remaining_qty, trade.tp2_price, fee_pct)
            )
            _close(trade, trade.tp2_price, "TP2", ts)

    # Timeout por tiempo máximo de hold (si no cerró por SL/TP en este tick).
    if (
        trade.status != TradeStatus.CLOSED
        and max_hold_seconds > 0
        and ts > 0
        and trade.opened_at > 0
        and ts - trade.opened_at >= max_hold_seconds
    ):
        qty = trade.remaining_qty
        actions.append(Action("FULL_CLOSE", price, qty, "TIMEOUT"))
        trade.realized_pnl += (
            (price - trade.entry_price) * qty * sign
            - _exit_fee(qty, price, fee_pct)
        )
        _close(trade, price, "TIMEOUT", ts)

    return actions
