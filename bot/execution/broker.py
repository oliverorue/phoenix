"""Broker live: órdenes reales vía ``BinanceFuturesClient``, con cierres seguros.

Convención: ``direction`` es el lado de la **posición** (LONG/SHORT). Todos los
cierres son ``reduceOnly``/``closePosition``. El cliente ya formatea precios y
cantidades al ``tickSize``/``stepSize`` del instrumento (ver F1).
"""

from __future__ import annotations

from typing import Optional

from bot.core.exchange_client import BinanceFuturesClient


def _order_side(direction: str) -> str:
    """Lado de la orden de apertura para una dirección de posición."""
    return "BUY" if direction.upper() == "LONG" else "SELL"


def _opposite(direction: str) -> str:
    """Lado de la orden de cierre para una dirección de posición."""
    return "SELL" if direction.upper() == "LONG" else "BUY"


class LiveBroker:
    """Fachada de ejecución real sobre ``BinanceFuturesClient``."""

    def __init__(self, client: BinanceFuturesClient) -> None:
        self._client = client

    async def set_margin(self, symbol: str, leverage: int = 1) -> None:
        """Margen aislado + apalancamiento para el símbolo."""
        await self._client.set_margin_type(symbol, "ISOLATED")
        await self._client.set_leverage(symbol, leverage)

    async def open_market(self, symbol: str, direction: str, qty: float) -> dict:
        """Apertura a mercado."""
        return await self._client.place_market_order(
            symbol, _order_side(direction), qty, reduce_only=False,
        )

    async def open_limit(
        self, symbol: str, direction: str, qty: float, price: float,
    ) -> dict:
        """Apertura con orden límite (maker, menos fees)."""
        return await self._client.place_limit_order(
            symbol, _order_side(direction), qty, price, reduce_only=False,
        )

    async def close_market(self, symbol: str, direction: str, qty: float) -> dict:
        """Cierre a mercado con reduceOnly (nunca abre posición inversa)."""
        return await self._client.place_market_order(
            symbol, _opposite(direction), qty, reduce_only=True,
        )

    async def set_sl(
        self, symbol: str, direction: str, stop_price: float,
        qty: Optional[float] = None,
    ) -> dict:
        """Stop Loss (STOP_MARKET). Sin ``qty`` cierra toda la posición."""
        return await self._client.set_stop_loss(symbol, direction, stop_price, qty=qty)

    async def set_tp(
        self, symbol: str, direction: str, stop_price: float,
        qty: Optional[float] = None,
    ) -> dict:
        """Take Profit (TAKE_PROFIT_MARKET). Sin ``qty`` cierra toda la posición."""
        return await self._client.place_take_profit_market_order(
            symbol, direction, stop_price, qty=qty,
        )

    async def move_sl(self, symbol: str, direction: str, stop_price: float) -> dict:
        """Cancela el SL actual y coloca uno nuevo (ej. a breakeven)."""
        return await self._client.update_stop_loss(symbol, direction, stop_price)

    async def fills(
        self, symbol: str, order_id: Optional[str] = None,
    ) -> list[dict]:
        """Fills ejecutados (con PnL realizado) para journaling live."""
        return await self._client.get_fills(symbol, order_id=order_id)

    async def cancel_all(self, symbol: str) -> None:
        await self._client.cancel_all_open_orders(symbol)

    async def position(self, symbol: str) -> Optional[dict]:
        positions = await self._client.get_positions(symbol)
        return positions[0] if positions else None

    async def order(self, symbol: str, order_id: str) -> dict:
        return await self._client.get_order_status(symbol, order_id)

    async def balance(self) -> dict:
        return await self._client.get_account_balance("USDT")
