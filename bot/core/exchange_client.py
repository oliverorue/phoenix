"""
exchange_client.py — Exchange abstraction layer for Phoenix v5.0.

Provides an ``ExchangeClient`` Protocol defining the interface for any exchange
adapter, and a concrete ``BinanceFuturesClient`` that implements it using
**python-binance** (v1.x) for Binance USDⓈ-M Futures.

Usage
-----
    client = BinanceFuturesClient(api_key="...", api_secret="...", testnet=True)
    price = await client.get_ticker_price("BTCUSDT")
"""

import asyncio
import json
import math
from typing import Any, Optional, Callable, Protocol, cast

import websockets as ws
from loguru import logger
from binance import Client
from binance.exceptions import (
    BinanceAPIException,
    BinanceRequestException,
)

from bot.core.exceptions import ExecutionException, APIException
from bot.core.timeframes import normalize_interval


# ═══════════════════════════════════════════════════════════════════════
#  Protocol — Interfaz común para cualquier exchange
# ═══════════════════════════════════════════════════════════════════════


class ExchangeClient(Protocol):
    """Protocolo que define la interfaz común para cualquier exchange.

    Usa ``typing.Protocol`` en lugar de ABC para permitir duck typing:
    cualquier clase que implemente estos métodos es un ExchangeClient
    válido sin necesidad de herencia explícita.
    """

    # ── Órdenes ─────────────────────────────────────────────────────
    async def place_market_order(
        self, symbol: str, side: str, qty: float,
        reduce_only: bool = False,
        client_order_id: Optional[str] = None,
    ) -> dict[str, Any]:
        ...

    async def place_limit_order(
        self, symbol: str, side: str, qty: float, price: float,
        reduce_only: bool = False,
        client_order_id: Optional[str] = None,
    ) -> dict[str, Any]:
        ...

    async def place_stop_market_order(
        self, symbol: str, side: str, qty: float, stop_price: float,
        reduce_only: bool = False,
    ) -> dict[str, Any]:
        ...

    async def place_take_profit_market_order(
        self, symbol: str, side: str, stop_price: float,
        qty: Optional[float] = None,
        client_order_id: Optional[str] = None,
    ) -> dict[str, Any]:
        ...

    async def cancel_order(self, symbol: str, order_id: str) -> dict[str, Any]:
        ...

    async def cancel_all_open_orders(self, symbol: str) -> None:
        ...

    # ── Posiciones y Órdenes ────────────────────────────────────────
    async def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        ...

    async def get_positions(self, symbol: str) -> list[dict[str, Any]]:
        ...

    async def get_all_positions(self) -> list[dict[str, Any]]:
        ...

    async def get_account_balance(self, asset: str = "USDT") -> dict[str, Any]:
        ...

    async def get_order_status(self, symbol: str, order_id: str) -> dict[str, Any]:
        ...

    async def get_fills(
        self, symbol: str, order_id: Optional[str] = None, limit: int = 100,
    ) -> list[dict[str, Any]]:
        ...

    # ── Configuración ───────────────────────────────────────────────
    async def set_leverage(self, symbol: str, leverage: int) -> None:
        ...

    async def set_margin_type(
        self, symbol: str, margin_type: str = "ISOLATED",
    ) -> None:
        ...

    # ── Stop Loss / Take Profit ─────────────────────────────────────
    async def set_stop_loss(
        self, symbol: str, side: str, stop_price: float,
        qty: Optional[float] = None,
        client_order_id: Optional[str] = None,
    ) -> dict[str, Any]:
        ...

    async def update_stop_loss(
        self, symbol: str, side: str, stop_price: float,
        client_order_id: Optional[str] = None,
    ) -> dict[str, Any]:
        ...

    # ── Trailing Stop (client-side) ─────────────────────────────────
    async def set_trailing_stop(
        self, symbol: str, side: str, distance_pct: float,
        activation_price: float,
    ) -> dict[str, Any]:
        ...

    # ── Market Data ─────────────────────────────────────────────────
    async def get_klines(
        self, symbol: str, interval: str, limit: int,
    ) -> list[dict[str, Any]]:
        ...

    async def get_funding_rate(self, symbol: str) -> Optional[dict[str, Any]]:
        ...

    async def get_open_interest(self, symbol: str) -> Optional[dict[str, Any]]:
        ...

    async def get_instrument_info(self, symbol: str) -> dict[str, Any]:
        ...

    async def get_ticker_price(self, symbol: str) -> float:
        ...

    # ── WebSocket ───────────────────────────────────────────────────
    async def subscribe_ws(
        self, streams: list[str],
        callback: Callable[[dict[str, Any]], Any],
    ) -> None:
        ...

    async def unsubscribe_ws(self, streams: list[str]) -> None:
        ...

    async def close_ws(self) -> None:
        ...

    # ── Utilidades ──────────────────────────────────────────────────
    async def ping(self) -> bool:
        ...


# ═══════════════════════════════════════════════════════════════════════
#  Excepciones específicas del módulo
# ═══════════════════════════════════════════════════════════════════════


class OrderException(ExecutionException):
    """Error específico al enviar, modificar o cancelar una orden."""


class MarketDataException(APIException):
    """Error al obtener datos de mercado desde el exchange."""


# ═══════════════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════════════

_WS_BASE_MAIN = "wss://fstream.binance.com"
_WS_BASE_TESTNET = "wss://stream.binancefuture.com"


# ═══════════════════════════════════════════════════════════════════════
#  BinanceFuturesClient — Implementación concreta para Binance USDⓈ-M
# ═══════════════════════════════════════════════════════════════════════


class BinanceFuturesClient:
    """Cliente concreto para Binance USDⓈ-M Futures.

    Implementa el protocolo ``ExchangeClient`` usando **python-binance**
    (v1.x). Maneja autenticación, formateo de cantidades según el
    *stepSize* del instrumento, cacheo de metadatos, y reconexión de
    WebSocket.

    Parameters
    ----------
    api_key : str
        API Key generada desde Binance.
    api_secret : str
        API Secret asociado a la API Key.
    testnet : bool
        ``True`` → apunta a https://testnet.binancefuture.com (por
        defecto).
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = True,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet

        # ── REST Client ─────────────────────────────────────────────
        self._rest = Client(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
            requests_params={"timeout": 30},
        )

        # ── WebSocket ───────────────────────────────────────────────
        self._ws_conn: "Optional[ws.WebSocketClientProtocol]" = None  # type: ignore[name-defined]
        self._ws_task: Optional[asyncio.Task[None]] = None
        self._ws_callback: Optional[Callable[[dict[str, Any]], Any]] = None
        self._ws_running = False

        # ── Cache de instrumentos ───────────────────────────────────
        self._instrument_cache: dict[str, dict[str, Any]] = {}

    # ─────────────────────────────────────────────────────────────────
    #  Helpers internos
    # ─────────────────────────────────────────────────────────────────

    def _resolve_error(
        self,
        exc: BinanceAPIException,
        context: str = "",
    ) -> "ExecutionException | APIException":
        """Traduce un ``BinanceAPIException`` a una excepción del bot.

        Códigos conocidos manejados:
            - ``-4058`` — Leverage unchanged (benigno).
            - ``-4046`` — Margin type unchanged (benigno).
            - ``-2010`` — Insufficient balance.
            - ``-2011`` — Order rejected.
            - ``-1013`` — Filter failure (quantity/price step).
            - ``-1121`` — Invalid symbol.
        """
        if exc.code in (-4058, -4046):
            return APIException(
                f"{context} (no change needed): {exc.message}",
            )

        if exc.code in (-2010, -2011, -1013):
            return OrderException(
                f"{context} (order error [{exc.code}]): {exc.message}",
            )

        return APIException(
            f"{context} (API error [{exc.code}]): {exc.message}",
        )

    async def _ensure_instrument_cache(self, symbol: str) -> None:
        """Garantiza que el instrumento está cacheado."""
        norm = self._normalize_symbol(symbol)
        if norm not in self._instrument_cache:
            await self.get_instrument_info(norm)

    def _format_quantity(self, symbol: str, qty: float) -> str:
        """Trunca *qty* al *stepSize* del instrumento (sin redondear).

        Binance rechaza órdenes cuya cantidad no sea múltiplo exacto
        del *stepSize* definido en el filtro ``LOT_SIZE``.
        Se usa ``math.floor`` (truncado) en lugar de ``round`` por
        seguridad.
        """
        sym = self._normalize_symbol(symbol)
        info = self._instrument_cache.get(sym, {})
        step_size = float(info.get("step_size", 0.001))
        precision = info.get("quantity_precision", 3)
        truncated = math.floor(qty / step_size) * step_size
        return f"{truncated:.{precision}f}"

    def _format_price(self, symbol: str, price: float) -> str:
        """Trunca *price* al *tickSize* del instrumento (sin redondear).

        Binance rechaza órdenes cuyo precio no sea múltiplo del *tickSize*
        definido en el filtro ``PRICE_FILTER``. Se usa ``math.floor``
        (truncado) en lugar de ``round`` para no cruzar el nivel de precio.
        """
        sym = self._normalize_symbol(symbol)
        info = self._instrument_cache.get(sym, {})
        tick_size = float(info.get("tick_size", 0.00001))
        precision = info.get("price_precision", 8)
        if tick_size <= 0:
            tick_size = 0.00001
        truncated = math.floor(price / tick_size + 1e-9) * tick_size
        return f"{truncated:.{precision}f}"

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        """Normaliza a mayúsculas (REST)."""
        return symbol.upper()

    @staticmethod
    def _ws_symbol(symbol: str) -> str:
        """Minúsculas para streams WebSocket."""
        return symbol.lower()

    # ─────────────────────────────────────────────────────────────────
    #  Órdenes
    # ─────────────────────────────────────────────────────────────────

    async def place_market_order(
        self, symbol: str, side: str, qty: float,
        reduce_only: bool = False,
        client_order_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Coloca una orden Market.

        Parameters
        ----------
        symbol : str
            Par de trading (ej. ``'BTCUSDT'``).
        side : str
            ``'BUY'`` o ``'SELL'``.
        qty : float
            Cantidad en unidades del activo base.

        Returns
        -------
        dict
            Respuesta de Binance (contiene ``orderId``, ``status``, …).

        Raises
        ------
        OrderException
            Si la orden es rechazada por filtros o saldo insuficiente.
        APIException
            Si hay un error de comunicación.
        """
        sym = self._normalize_symbol(symbol)
        await self._ensure_instrument_cache(sym)
        qty_str = self._format_quantity(sym, qty)

        params: dict[str, Any] = {
            "symbol": sym,
            "side": side,
            "type": "MARKET",
            "quantity": qty_str,
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        if client_order_id:
            params["newClientOrderId"] = client_order_id

        try:
            return await asyncio.to_thread(
                self._rest.futures_create_order, **params,
            )
        except BinanceAPIException as exc:
            raise self._resolve_error(
                exc, f"place_market_order({sym}, {side}, {qty})",
            ) from exc
        except BinanceRequestException as exc:
            raise APIException(
                f"place_market_order({sym}) request error: {exc}",
            ) from exc

    async def place_limit_order(
        self, symbol: str, side: str, qty: float, price: float,
        reduce_only: bool = False,
        client_order_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Coloca una orden Limit (GTC).

        Parameters
        ----------
        symbol : str
            Par de trading.
        side : str
            ``'BUY'`` o ``'SELL'``.
        qty : float
            Cantidad.
        price : float
            Precio límite.
        reduce_only : bool
            Si ``True``, solo puede reducir la posición actual.

        Returns
        -------
        dict
            Respuesta de Binance.

        Raises
        ------
        OrderException
            Si la orden es rechazada.
        APIException
            Si hay un error de comunicación.
        """
        sym = self._normalize_symbol(symbol)
        await self._ensure_instrument_cache(sym)
        qty_str = self._format_quantity(sym, qty)

        params: dict[str, Any] = {
            "symbol": sym,
            "side": side,
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": qty_str,
            "price": self._format_price(sym, price),
            "reduceOnly": "true" if reduce_only else "false",
        }
        if client_order_id:
            params["newClientOrderId"] = client_order_id

        try:
            return await asyncio.to_thread(
                self._rest.futures_create_order, **params,
            )
        except BinanceAPIException as exc:
            raise self._resolve_error(
                exc, f"place_limit_order({sym}, {side}, {qty} @ {price})",
            ) from exc
        except BinanceRequestException as exc:
            raise APIException(
                f"place_limit_order({sym}) request error: {exc}",
            ) from exc

    async def place_stop_market_order(
        self, symbol: str, side: str, qty: float, stop_price: float,
        reduce_only: bool = False,
    ) -> dict[str, Any]:
        """Coloca una orden Stop Market (``STOP_MARKET``).

        Parameters
        ----------
        symbol : str
            Par de trading.
        side : str
            ``'BUY'`` o ``'SELL'``.
        qty : float
            Cantidad.
        stop_price : float
            Precio que activa la orden.
        reduce_only : bool
            Si ``True``, solo puede reducir la posición actual.

        Returns
        -------
        dict
            Respuesta de Binance.

        Raises
        ------
        OrderException
            Si la orden es rechazada.
        APIException
            Si hay un error de comunicación.
        """
        sym = self._normalize_symbol(symbol)
        await self._ensure_instrument_cache(sym)
        qty_str = self._format_quantity(sym, qty)

        try:
            return await asyncio.to_thread(
                self._rest.futures_create_order,
                symbol=sym,
                side=side,
                type="STOP_MARKET",
                quantity=qty_str,
                stopPrice=self._format_price(sym, stop_price),
                reduceOnly="true" if reduce_only else "false",
            )
        except BinanceAPIException as exc:
            raise self._resolve_error(
                exc,
                f"place_stop_market_order({sym}, {side}, {qty} @ stop {stop_price})",
            ) from exc
        except BinanceRequestException as exc:
            raise APIException(
                f"place_stop_market_order({sym}) request error: {exc}",
            ) from exc

    async def cancel_order(self, symbol: str, order_id: str) -> dict[str, Any]:
        """Cancela una orden específica por su ``orderId``.

        Returns
        -------
        dict
            Respuesta de Binance.

        Raises
        ------
        OrderException
            Si la orden no existe o ya fue cancelada.
        APIException
            Si hay un error de comunicación.
        """
        sym = self._normalize_symbol(symbol)
        try:
            return await asyncio.to_thread(
                self._rest.futures_cancel_order,
                symbol=sym, orderId=order_id,
            )
        except BinanceAPIException as exc:
            raise self._resolve_error(
                exc, f"cancel_order({sym}, {order_id})",
            ) from exc
        except BinanceRequestException as exc:
            raise APIException(
                f"cancel_order({sym}) request error: {exc}",
            ) from exc

    async def cancel_all_open_orders(self, symbol: str) -> None:
        """Cancela **todas** las órdenes abiertas para un símbolo.

        Raises
        ------
        APIException
            Si hay un error de comunicación.
        """
        sym = self._normalize_symbol(symbol)
        try:
            await asyncio.to_thread(
                self._rest.futures_cancel_all_open_orders, symbol=sym,
            )
            logger.debug("All open orders cancelled for {}", sym)
        except BinanceAPIException as exc:
            raise self._resolve_error(
                exc, f"cancel_all_open_orders({sym})",
            ) from exc
        except BinanceRequestException as exc:
            raise APIException(
                f"cancel_all_open_orders({sym}) request error: {exc}",
            ) from exc

    # ─────────────────────────────────────────────────────────────────
    #  Posiciones y Órdenes abiertas
    # ─────────────────────────────────────────────────────────────────

    async def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        """Retorna todas las órdenes abiertas para un símbolo.

        Returns
        -------
        list[dict]
            Lista de órdenes con estado ``'NEW'`` o ``'PARTIALLY_FILLED'``.
        """
        sym = self._normalize_symbol(symbol)
        try:
            orders = await asyncio.to_thread(
                self._rest.futures_get_open_orders, symbol=sym,
            )
            return [
                o for o in orders
                if o.get("status") in ("NEW", "PARTIALLY_FILLED")
            ]
        except BinanceAPIException as exc:
            raise self._resolve_error(
                exc, f"get_open_orders({sym})",
            ) from exc
        except BinanceRequestException as exc:
            raise APIException(
                f"get_open_orders({sym}) request error: {exc}",
            ) from exc

    @staticmethod
    def _standardize_position(pos: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Convierte una posición cruda de Binance a un dict estandarizado.

        Devuelve ``None`` si la posición está cerrada (``positionAmt == 0``).
        """
        size = float(pos.get("positionAmt", "0"))
        if size == 0:
            return None
        return {
            "symbol": pos.get("symbol"),
            "size": abs(size),
            "side": "LONG" if size > 0 else "SHORT",
            "avgPrice": float(pos.get("entryPrice", "0")),
            "unrealizedPnL": float(pos.get("unRealizedProfit", "0")),
        }

    async def get_positions(self, symbol: str) -> list[dict[str, Any]]:
        """Retorna las posiciones abiertas para un símbolo.

        Returns
        -------
        list[dict]
            Lista de dicts estandarizados con las claves:
            ``symbol``, ``size``, ``side``, ``avgPrice``, ``unrealizedPnL``.
            Las posiciones con ``size == 0`` se omiten.
        """
        sym = self._normalize_symbol(symbol)
        try:
            positions = await asyncio.to_thread(
                self._rest.futures_position_information, symbol=sym,
            )
        except BinanceAPIException as exc:
            raise self._resolve_error(
                exc, f"get_positions({sym})",
            ) from exc
        except BinanceRequestException as exc:
            raise APIException(
                f"get_positions({sym}) request error: {exc}",
            ) from exc
        result: list[dict[str, Any]] = []
        for pos in positions:
            std = self._standardize_position(pos)
            if std is not None:
                result.append(std)
        return result

    async def get_all_positions(self) -> list[dict[str, Any]]:
        """Retorna **todas** las posiciones abiertas (todos los símbolos).

        Útil para el kill switch (aplanar todo) y para el resumen del
        dashboard. Mismo formato estandarizado que :meth:`get_positions`.
        """
        try:
            positions = await asyncio.to_thread(
                self._rest.futures_position_information,
            )
        except BinanceAPIException as exc:
            raise self._resolve_error(exc, "get_all_positions") from exc
        except BinanceRequestException as exc:
            raise APIException(
                f"get_all_positions request error: {exc}",
            ) from exc
        result: list[dict[str, Any]] = []
        for pos in positions:
            std = self._standardize_position(pos)
            if std is not None:
                result.append(std)
        return result

    async def get_account_balance(self, asset: str = "USDT") -> dict[str, Any]:
        """Retorna el balance de una moneda en la cuenta de futuros.

        Returns
        -------
        dict
            Con ``asset``, ``balance`` (wallet) y ``available`` (margen
            disponible para abrir). Cero si el activo no aparece.
        """
        try:
            balances = await asyncio.to_thread(
                self._rest.futures_account_balance,
            )
        except BinanceAPIException as exc:
            raise self._resolve_error(
                exc, f"get_account_balance({asset})",
            ) from exc
        except BinanceRequestException as exc:
            raise APIException(
                f"get_account_balance({asset}) request error: {exc}",
            ) from exc
        for b in balances:
            if b.get("asset") == asset:
                return {
                    "asset": asset,
                    "balance": float(b.get("balance", "0") or 0),
                    "available": float(b.get("availableBalance", "0") or 0),
                }
        return {"asset": asset, "balance": 0.0, "available": 0.0}

    async def get_order_status(self, symbol: str, order_id: str) -> dict[str, Any]:
        """Retorna el estado de una orden por su ``orderId``.

        Returns
        -------
        dict
            Estandarizado: ``order_id``, ``symbol``, ``status``, ``side``,
            ``type``, ``qty``, ``executed_qty``, ``avg_price``,
            ``cum_quote``, ``reduce_only``, ``price``, ``stop_price``.
        """
        sym = self._normalize_symbol(symbol)
        try:
            o = await asyncio.to_thread(
                self._rest.futures_get_order, symbol=sym, orderId=order_id,
            )
        except BinanceAPIException as exc:
            raise self._resolve_error(
                exc, f"get_order_status({sym}, {order_id})",
            ) from exc
        except BinanceRequestException as exc:
            raise APIException(
                f"get_order_status({sym}) request error: {exc}",
            ) from exc
        return {
            "order_id": o.get("orderId"),
            "symbol": o.get("symbol"),
            "status": o.get("status"),
            "side": o.get("side"),
            "type": o.get("type"),
            "qty": float(o.get("origQty", "0") or 0),
            "executed_qty": float(o.get("executedQty", "0") or 0),
            "avg_price": float(o.get("avgPrice", "0") or 0),
            "cum_quote": float(o.get("cumQuote", "0") or 0),
            "reduce_only": o.get("reduceOnly", False),
            "price": float(o.get("price", "0") or 0),
            "stop_price": float(o.get("stopPrice", "0") or 0),
        }

    async def get_fills(
        self, symbol: str, order_id: Optional[str] = None, limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Retorna los fills (trades ejecutados) de un símbolo.

        Si ``order_id`` se pasa, filtra solo los fills de esa orden. Sirve
        para el journaling automático (precio real, comisión, PnL realizado).

        Returns
        -------
        list[dict]
            Con ``order_id``, ``price``, ``qty``, ``commission``,
            ``realized_pnl``, ``side``, ``time``.
        """
        sym = self._normalize_symbol(symbol)
        try:
            trades = await asyncio.to_thread(
                self._rest.futures_account_trades, symbol=sym, limit=limit,
            )
        except BinanceAPIException as exc:
            raise self._resolve_error(exc, f"get_fills({sym})") from exc
        except BinanceRequestException as exc:
            raise APIException(
                f"get_fills({sym}) request error: {exc}",
            ) from exc
        result: list[dict[str, Any]] = []
        for t in trades:
            if order_id is not None and str(t.get("orderId")) != str(order_id):
                continue
            result.append({
                "order_id": t.get("orderId"),
                "price": float(t.get("price", "0") or 0),
                "qty": float(t.get("qty", "0") or 0),
                "commission": float(t.get("commission", "0") or 0),
                "realized_pnl": float(t.get("realizedPnl", "0") or 0),
                "side": t.get("side"),
                "time": t.get("time"),
            })
        return result

    # ─────────────────────────────────────────────────────────────────
    #  Configuración
    # ─────────────────────────────────────────────────────────────────

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        """Cambia el apalancamiento para un símbolo.

        Captura el error ``-4058`` (leverage ya establecido) sin
        relanzar — no es un error real.
        """
        sym = self._normalize_symbol(symbol)
        try:
            await asyncio.to_thread(
                self._rest.futures_change_leverage,
                symbol=sym, leverage=leverage,
            )
            logger.debug("Leverage set to {}x for {}", leverage, sym)
        except BinanceAPIException as exc:
            if exc.code == -4058:
                logger.debug(
                    "Leverage already {}x for {}, skipping", leverage, sym,
                )
                return
            raise self._resolve_error(
                exc, f"set_leverage({sym}, {leverage})",
            ) from exc

    async def set_margin_type(
        self, symbol: str, margin_type: str = "ISOLATED",
    ) -> None:
        """Cambia el tipo de margen (``ISOLATED`` / ``CROSSED``).

        Captura el error ``-4046`` (no change needed) sin relanzar.
        """
        sym = self._normalize_symbol(symbol)
        try:
            await asyncio.to_thread(
                self._rest.futures_change_margin_type,
                symbol=sym,
                marginType=margin_type,
            )
            logger.debug("Margin type set to {} for {}", margin_type, sym)
        except BinanceAPIException as exc:
            if exc.code == -4046:
                logger.debug(
                    "Margin type already {} for {}, skipping",
                    margin_type, sym,
                )
                return
            raise self._resolve_error(
                exc, f"set_margin_type({sym}, {margin_type})",
            ) from exc

    # ─────────────────────────────────────────────────────────────────
    #  Stop Loss / Take Profit
    # ─────────────────────────────────────────────────────────────────

    async def set_stop_loss(
        self, symbol: str, side: str, stop_price: float,
        qty: Optional[float] = None,
        client_order_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Establece un Stop Loss usando ``STOP_MARKET``.

        Parameters
        ----------
        symbol : str
            Par de trading.
        side : str
            Lado de la **posición actual** (``'LONG'`` o ``'SHORT'``).
        stop_price : float
            Precio que activa el stop.
        qty : float | None
            Cantidad a cerrar. Si es ``None``, usa
            ``closePosition="true"`` (cierra toda la posición).

        Returns
        -------
        dict
            Respuesta de Binance.

        Note
        ----
        El ``side`` del parámetro es el lado de la **posición**, no el
        de la orden. Internamente se convierte al opuesto:
        ``LONG → SELL``, ``SHORT → BUY``.
        """
        sym = self._normalize_symbol(symbol)
        opposite_side = "SELL" if side.upper() == "LONG" else "BUY"
        await self._ensure_instrument_cache(sym)

        try:
            params: dict[str, Any] = {
                "symbol": sym,
                "side": opposite_side,
                "type": "STOP_MARKET",
                "stopPrice": self._format_price(sym, stop_price),
            }
            if qty is not None:
                params["quantity"] = self._format_quantity(sym, qty)
                params["reduceOnly"] = "true"
            else:
                params["closePosition"] = "true"
            if client_order_id:
                params["newClientOrderId"] = client_order_id

            result = await asyncio.to_thread(
                self._rest.futures_create_order, **params,
            )

            logger.info(
                "SL set for {} at {} (side={})", sym, stop_price, opposite_side,
            )
            return cast(dict[str, Any], result)
        except BinanceAPIException as exc:
            raise self._resolve_error(
                exc, f"set_stop_loss({sym}, {side}, stop={stop_price})",
            ) from exc
        except BinanceRequestException as exc:
            raise APIException(
                f"set_stop_loss({sym}) request error: {exc}",
            ) from exc

    async def place_take_profit_market_order(
        self, symbol: str, side: str, stop_price: float,
        qty: Optional[float] = None,
        client_order_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Establece un Take Profit usando ``TAKE_PROFIT_MARKET``.

        Parameters
        ----------
        symbol : str
            Par de trading.
        side : str
            Lado de la **posición actual** (``'LONG'`` o ``'SHORT'``).
        stop_price : float
            Precio que dispara la toma de ganancia.
        qty : float | None
            Cantidad a cerrar. Si es ``None``, usa ``closePosition="true"``
            (cierra toda la posición).

        Returns
        -------
        dict
            Respuesta de Binance.

        Note
        ----
        Igual que :meth:`set_stop_loss`, ``side`` es el lado de la posición,
        no el de la orden (se convierte al opuesto). El cierre siempre es
        ``reduceOnly``/``closePosition``: nunca abre posición inversa.
        """
        sym = self._normalize_symbol(symbol)
        opposite_side = "SELL" if side.upper() == "LONG" else "BUY"
        await self._ensure_instrument_cache(sym)

        try:
            params: dict[str, Any] = {
                "symbol": sym,
                "side": opposite_side,
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": self._format_price(sym, stop_price),
            }
            if qty is not None:
                params["quantity"] = self._format_quantity(sym, qty)
                params["reduceOnly"] = "true"
            else:
                params["closePosition"] = "true"
            if client_order_id:
                params["newClientOrderId"] = client_order_id

            result = await asyncio.to_thread(
                self._rest.futures_create_order, **params,
            )
            logger.info(
                "TP set for {} at {} (side={})", sym, stop_price, opposite_side,
            )
            return cast(dict[str, Any], result)
        except BinanceAPIException as exc:
            raise self._resolve_error(
                exc,
                f"place_take_profit_market_order({sym}, {side}, tp={stop_price})",
            ) from exc
        except BinanceRequestException as exc:
            raise APIException(
                f"place_take_profit_market_order({sym}) request error: {exc}",
            ) from exc

    async def update_stop_loss(
        self, symbol: str, side: str, stop_price: float,
        client_order_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Actualiza un Stop Loss cancelando el anterior y creando uno nuevo.

        Busca órdenes ``STOP_MARKET`` abiertas con el side opuesto,
        las cancela, y crea el nuevo SL.

        Parameters
        ----------
        symbol : str
            Par de trading.
        side : str
            Lado de la posición actual (``'LONG'`` o ``'SHORT'``).
        stop_price : float
            Nuevo precio de activación.

        Returns
        -------
        dict
            Respuesta de Binance para el nuevo SL.
        """
        sym = self._normalize_symbol(symbol)
        opposite_side = "SELL" if side.upper() == "LONG" else "BUY"

        # 1. Cancelar STOP_MARKET existentes del lado opuesto
        try:
            open_orders = await self.get_open_orders(sym)
            for order in open_orders:
                if (
                    order.get("type") == "STOP_MARKET"
                    and order.get("side") == opposite_side
                ):
                    try:
                        await asyncio.to_thread(
                            self._rest.futures_cancel_order,
                            symbol=sym,
                            orderId=order["orderId"],
                        )
                        logger.debug(
                            "Cancelled old SL order {} for {}",
                            order["orderId"], sym,
                        )
                    except BinanceAPIException:
                        logger.warning(
                            "Could not cancel old SL {} for {}",
                            order.get("orderId"), sym,
                        )

            # 2. Crear nuevo SL
            return await self.set_stop_loss(
                sym, side, stop_price, client_order_id=client_order_id,
            )
        except BinanceAPIException as exc:
            raise self._resolve_error(
                exc, f"update_stop_loss({sym}, {side}, stop={stop_price})",
            ) from exc

    # ─────────────────────────────────────────────────────────────────
    #  Trailing Stop (client-side)
    # ─────────────────────────────────────────────────────────────────

    async def set_trailing_stop(
        self, symbol: str, side: str, distance_pct: float,
        activation_price: float,
    ) -> dict[str, Any]:
        """Implementa un trailing stop **client-side**.

        Binance USDⓈ-M Futures **no** soporta trailing stops nativos.
        Este método coloca un ``STOP_MARKET`` inicial en
        ``activation_price`` y documenta que el **caller** debe invocar
        :meth:`update_stop_loss` periódicamente para ajustar el precio
        según la distancia deseada.

        Parameters
        ----------
        symbol : str
            Par de trading.
        side : str
            Lado de la posición (``'LONG'`` o ``'SHORT'``).
        distance_pct : float
            Porcentaje de distancia (ej. ``0.5`` = 0.5 %). Se incluye
            en el log pero no se usa en la orden inicial.
        activation_price : float
            Precio desde el cual se activa el trailing.

        Returns
        -------
        dict
            Respuesta de Binance (ver :meth:`set_stop_loss`).
        """
        logger.info(
            "Trailing stop client-side para {} (dist={}%, act={}). "
            "Requiere update_stop_loss() periódico del caller.",
            symbol, distance_pct, activation_price,
        )
        return await self.set_stop_loss(symbol, side, activation_price)

    # ─────────────────────────────────────────────────────────────────
    #  Market Data
    # ─────────────────────────────────────────────────────────────────

    async def get_klines(
        self, symbol: str, interval: str, limit: int,
    ) -> list[dict[str, Any]]:
        """Obtiene velas (klines) históricas.

        Returns
        -------
        list[dict]
            Lista de dicts estandarizados con las claves:
            ``open_time``, ``open``, ``high``, ``low``, ``close``,
            ``volume``, ``close_time``, ``quote_volume``, ``count``,
            ``taker_buy_volume``, ``taker_buy_quote_volume``.
        """
        sym = self._normalize_symbol(symbol)
        binance_interval = normalize_interval(interval)
        try:
            raw = await asyncio.to_thread(
                self._rest.futures_klines,
                symbol=sym,
                interval=binance_interval,
                limit=limit,
            )
            return [
                {
                    "open_time": k[0],
                    "open": k[1],
                    "high": k[2],
                    "low": k[3],
                    "close": k[4],
                    "volume": k[5],
                    "close_time": k[6],
                    "quote_volume": k[7],
                    "count": k[8],
                    "taker_buy_volume": k[9],
                    "taker_buy_quote_volume": k[10],
                    "ignore": k[11],
                }
                for k in raw
            ]
        except BinanceAPIException as exc:
            raise MarketDataException(
                f"get_klines({sym}, {binance_interval}, {limit}): "
                f"[{exc.code}] {exc.message}",
            ) from exc
        except BinanceRequestException as exc:
            raise MarketDataException(
                f"get_klines({sym}) request error: {exc}",
            ) from exc

    async def get_funding_rate(
        self, symbol: str,
    ) -> Optional[dict[str, Any]]:
        """Obtiene la tasa de funding actual del símbolo.

        Returns
        -------
        dict | None
            Contiene ``symbol``, ``funding_rate``, ``funding_time``,
            ``mark_price``. ``None`` si no hay datos.
        """
        sym = self._normalize_symbol(symbol)
        try:
            rates = await asyncio.to_thread(
                self._rest.futures_funding_rate, symbol=sym, limit=1,
            )
            if rates:
                return {
                    "symbol": rates[0].get("symbol"),
                    "funding_rate": float(rates[0].get("fundingRate", "0")),
                    "funding_time": int(rates[0].get("fundingTime", 0)),
                    "mark_price": float(rates[0].get("markPrice", "0")),
                }
            return None
        except BinanceAPIException as exc:
            raise MarketDataException(
                f"get_funding_rate({sym}): [{exc.code}] {exc.message}",
            ) from exc
        except BinanceRequestException as exc:
            raise MarketDataException(
                f"get_funding_rate({sym}) request error: {exc}",
            ) from exc

    async def get_open_interest(
        self, symbol: str,
    ) -> Optional[dict[str, Any]]:
        """Obtiene el open interest actual del símbolo.

        Returns
        -------
        dict | None
            Contiene ``symbol``, ``open_interest``, ``time``.
        """
        sym = self._normalize_symbol(symbol)
        try:
            oi = await asyncio.to_thread(
                self._rest.futures_open_interest, symbol=sym,
            )
            return {
                "symbol": oi.get("symbol"),
                "open_interest": float(oi.get("openInterest", "0")),
                "time": int(oi.get("time", 0)),
            }
        except BinanceAPIException as exc:
            raise MarketDataException(
                f"get_open_interest({sym}): [{exc.code}] {exc.message}",
            ) from exc
        except BinanceRequestException as exc:
            raise MarketDataException(
                f"get_open_interest({sym}) request error: {exc}",
            ) from exc

    async def get_instrument_info(self, symbol: str) -> dict[str, Any]:
        """Obtiene la especificación completa del instrumento.

        Cachea el resultado para evitar llamadas repetidas a la API.
        Extrae y estructura los filtros ``LOT_SIZE``, ``NOTIONAL``
        (o su variante legacy ``MIN_NOTIONAL``), ``PRICE_FILTER`` y las
        precisiones del símbolo solicitado.

        Returns
        -------
        dict
            Contiene: ``symbol``, ``price_precision``,
            ``quantity_precision``, ``step_size``, ``min_qty``,
            ``max_qty``, ``min_notional``, ``max_notional``,
            ``tick_size``, ``min_price``, ``max_price``, ``filters``,
            ``raw``.
        """
        sym = self._normalize_symbol(symbol)

        if sym in self._instrument_cache:
            return self._instrument_cache[sym]

        try:
            info = await asyncio.to_thread(
                self._rest.futures_exchange_info,
            )
            symbols = info.get("symbols", [])
            raw_sym = next(
                (s for s in symbols if s.get("symbol") == sym),
                None,
            )
            if raw_sym is None:
                raise MarketDataException(
                    f"Instrumento no encontrado en exchangeInfo: {sym}"
                )

            filters = {f["filterType"]: f for f in raw_sym.get("filters", [])}

            lot_size = filters.get("LOT_SIZE", {})
            notional_filter = (
                filters.get("NOTIONAL")
                or filters.get("MIN_NOTIONAL")
                or {}
            )
            price_filter = filters.get("PRICE_FILTER", {})

            step_size = float(lot_size.get("stepSize", "0.001"))
            quantity_precision = raw_sym.get("quantityPrecision", 3)

            cached: dict[str, Any] = {
                "symbol": sym,
                "price_precision": raw_sym.get("pricePrecision", 8),
                "quantity_precision": quantity_precision,
                "step_size": step_size,
                "min_qty": float(lot_size.get("minQty", "0")),
                "max_qty": float(lot_size.get("maxQty", "0")),
                "min_notional": float(
                    notional_filter.get(
                        "minNotional",
                        notional_filter.get("notional", "0"),
                    )
                ),
                "max_notional": float(
                    notional_filter.get("maxNotional", "0")
                ),
                "tick_size": float(price_filter.get("tickSize", "0.00001")),
                "min_price": float(price_filter.get("minPrice", "0")),
                "max_price": float(price_filter.get("maxPrice", "0")),
                "filters": filters,
                "raw": raw_sym,
            }
            self._instrument_cache[sym] = cached
            return cached

        except BinanceAPIException as exc:
            raise MarketDataException(
                f"get_instrument_info({sym}): [{exc.code}] {exc.message}",
            ) from exc
        except BinanceRequestException as exc:
            raise MarketDataException(
                f"get_instrument_info({sym}) request error: {exc}",
            ) from exc

    async def get_ticker_price(self, symbol: str) -> float:
        """Obtiene el precio actual del símbolo.

        Returns
        -------
        float
            Precio actual.
        """
        sym = self._normalize_symbol(symbol)
        try:
            ticker = await asyncio.to_thread(
                self._rest.futures_symbol_ticker, symbol=sym,
            )
            return float(ticker.get("price", "0"))
        except BinanceAPIException as exc:
            raise MarketDataException(
                f"get_ticker_price({sym}): [{exc.code}] {exc.message}",
            ) from exc
        except BinanceRequestException as exc:
            raise MarketDataException(
                f"get_ticker_price({sym}) request error: {exc}",
            ) from exc

    # ─────────────────────────────────────────────────────────────────
    #  WebSocket
    # ─────────────────────────────────────────────────────────────────

    async def subscribe_ws(
        self, streams: list[str],
        callback: Callable[[dict[str, Any]], Any],
    ) -> None:
        """Se suscribe a streams de WebSocket de Binance Futures.

        Establece una conexión WebSocket combinada (``/stream``) que
        recibe todos los streams especificados. Cada mensaje JSON se
        pasa al *callback*.

        Si ya hay una conexión activa, la cierra y la reemplaza
        (python-binance no soporta múltiples conexiones WS fácilmente
        desde un solo cliente).

        Parameters
        ----------
        streams : list[str]
            Lista de nombres de stream en **minúsculas**
            (ej. ``['btcusdt@bookTicker', 'btcusdt@aggTrade']``).
        callback : Callable
            Función invocada con cada mensaje ``dict`` recibido.
        """
        self._ws_callback = callback

        # Cerrar conexión previa si existe
        if self._ws_conn is not None:
            await self.close_ws()

        base = _WS_BASE_TESTNET if self.testnet else _WS_BASE_MAIN
        stream_path = "/".join(streams)
        url = f"{base}/stream?streams={stream_path}"

        self._ws_running = True

        async def _reader() -> None:
            """Bucle interno que lee mensajes del WebSocket."""
            reconnect_delay = 1.0
            max_delay = 30.0
            while self._ws_running:
                try:
                    async with ws.connect(url) as conn:
                        self._ws_conn = conn
                        reconnect_delay = 1.0  # reset on success
                        async for raw in conn:
                            if not self._ws_running:
                                break
                            try:
                                msg = json.loads(raw)
                                if self._ws_callback:
                                    await self._ws_callback(msg)
                            except json.JSONDecodeError:
                                logger.warning(
                                    "WS non-JSON message ignored: {}",
                                    raw[:200],
                                )
                            except Exception:
                                logger.exception("Error in WS callback")
                except ws.ConnectionClosed:
                    logger.warning("WS connection closed, reconnecting...")
                except Exception:
                    logger.exception("WS unexpected error, reconnecting...")
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, max_delay)

        self._ws_task = asyncio.create_task(_reader())
        logger.info("WS subscribed to streams: {}", streams)

    @property
    def is_ws_connected(self) -> bool:
        """True si la conexión WebSocket está activa."""
        return self._ws_conn is not None and self._ws_running

    async def unsubscribe_ws(self, streams: list[str]) -> None:
        """Desuscribe streams.

        .. note::
           Las conexiones combinadas de Binance no soportan
           desuscripción selectiva por stream. Use :meth:`close_ws` +
           :meth:`subscribe_ws` para cambiar los streams activos.
        """
        logger.warning(
            "unsubscribe_ws: las conexiones combinadas no soportan "
            "desuscripción por stream. Use close_ws() + subscribe_ws() "
            "para reemplazar los streams.",
        )

    async def close_ws(self) -> None:
        """Cierra la conexión WebSocket y la tarea asociada."""
        self._ws_running = False

        if self._ws_task is not None and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None

        if self._ws_conn is not None:
            try:
                await self._ws_conn.close()
            except Exception:
                logger.exception("Error closing WS connection")
            finally:
                self._ws_conn = None

        self._ws_callback = None
        logger.info("WebSocket connection closed")

    # ─────────────────────────────────────────────────────────────────
    #  Utilidades
    # ─────────────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        """Verifica conectividad con la API REST de Binance.

        Returns
        -------
        bool
            ``True`` si la API responde correctamente.
        """
        try:
            await asyncio.to_thread(self._rest.futures_ping)
            return True
        except (BinanceAPIException, BinanceRequestException):
            return False
        except Exception:
            logger.exception("Ping failed unexpectedly")
            return False
