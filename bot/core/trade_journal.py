"""
Trade Journal: Registro en CSV de todas las operaciones.
"""
from loguru import logger
import csv
from datetime import datetime, timezone
from typing import Any
from config.loader import LOG_DIR

class TradeJournal:
    def __init__(self, journal_file: str = "trade_journal.csv", notifier: Any = None,
                 database: Any = None, audit: Any = None,
                 kelly_sizing: Any = None) -> None:
        self.filepath = LOG_DIR / journal_file
        self.notifier = notifier
        self.database = database
        self.audit = audit              # ← usar el audit que nos pasan, no la db
        self._kelly_sizing = kelly_sizing
        self._ensure_header()
        self._open_trade: dict[str, Any] = {}

    def _ensure_header(self) -> None:
        if not self.filepath.exists():
            with open(self.filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "trade_id", "symbol", "side",
                    "entry_time", "exit_time",
                    "entry_price", "exit_price",
                    "qty", "pnl_usdt", "pnl_pct",
                    "sl_initial", "sl_final",
                    "tp1", "tp2", "close_reason",
                    "duration_seconds",
                    "rsi_entry", "adx_entry", "atr_entry",
                    "max_runup_pct", "max_drawdown_pct",
                    "ma_spread_entry", "tp1_hit", "tp2_hit", "commission_usdt"
                ])

    def open(self, symbol: str, side: str, entry_price: float, qty: float,
             sl: float, tp1: float, tp2: float, indicators: dict[str, object] | None = None) -> None:
        if indicators is None:
            indicators = {}
        self._open_trade = {
            "trade_id": f"{side}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            "symbol": symbol,
            "side": side,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "entry_price": entry_price,
            "qty": qty,
            "initial_qty": qty,
            "realized_pnl_gross": 0.0,
            "sl_initial": sl,
            "tp1": tp1,
            "tp2": tp2,
            "rsi_entry": indicators.get("RSI", 0),
            "adx_entry": indicators.get("ADX", 0),
            "atr_entry": indicators.get("ATR", 0),
            "ma_spread_entry": indicators.get("MA_spread", 0),
            "tp1_hit": False,
            "tp2_hit": False,
            "commission_usdt": 0.0,
        }
        logger.info(f"📝 Trade abierto: {side} {symbol} @ {entry_price:.4f}")

    def update_runup_drawdown(self, current_price: float) -> None:
        """Actualiza máximo runup y drawdown mientras la posición está abierta."""
        if not self._open_trade:
            return
        entry = self._open_trade["entry_price"]
        side = self._open_trade["side"]
        if entry == 0:
            return
        if side == "LONG":
            runup = (current_price - entry) / entry * 100
            dd = (entry - current_price) / entry * 100
        else:
            runup = (entry - current_price) / entry * 100
            dd = (current_price - entry) / entry * 100
        if "max_runup_pct" not in self._open_trade:
            self._open_trade["max_runup_pct"] = 0.0
            self._open_trade["max_drawdown_pct"] = 0.0
        if runup > self._open_trade["max_runup_pct"]:
            self._open_trade["max_runup_pct"] = round(runup, 4)
        if dd > self._open_trade["max_drawdown_pct"]:
            self._open_trade["max_drawdown_pct"] = round(dd, 4)

    def close(self, exit_price: float, close_reason: str, sl_final: float | None = None) -> None:
        if not self._open_trade:
            return
        entry = self._open_trade["entry_price"]
        qty = self._open_trade["qty"]  # cantidad remanente
        initial_qty = self._open_trade.get("initial_qty", qty)
        side = self._open_trade["side"]
        entry_time = self._open_trade["entry_time"]
        exit_time = datetime.now(timezone.utc).isoformat()

        if side == "LONG":
            remaining_pnl = (exit_price - entry) * qty
        else:
            remaining_pnl = (entry - exit_price) * qty

        # PnL total = parciales ya realizados + remanente - comisiones.
        realized_gross = float(self._open_trade.get("realized_pnl_gross", 0.0))
        commission_total = float(self._open_trade.get("commission_usdt", 0.0))
        pnl_usdt = remaining_pnl + realized_gross - commission_total
        notional = entry * initial_qty
        pnl_pct = (pnl_usdt / notional * 100) if notional else 0.0

        # ── Notificar a KellySizing ──
        if self._kelly_sizing is not None:
            try:
                self._kelly_sizing.record_trade(pnl_usdt)
            except Exception as e:
                logger.error(f"Error notificando a KellySizing: {e}")

        try:
            t_open = datetime.fromisoformat(entry_time)
            t_close = datetime.fromisoformat(exit_time)
            duration = (t_close - t_open).total_seconds()
        except Exception:
            duration = 0

        with open(self.filepath, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                self._open_trade["trade_id"],
                self._open_trade["symbol"],
                self._open_trade["side"],
                entry_time,
                exit_time,
                entry,
                exit_price,
                initial_qty,
                round(pnl_usdt, 4),
                round(pnl_pct, 4),
                self._open_trade["sl_initial"],
                sl_final if sl_final is not None else "",
                self._open_trade["tp1"],
                self._open_trade["tp2"],
                close_reason,
                int(duration),
                self._open_trade.get("rsi_entry", 0),
                self._open_trade.get("adx_entry", 0),
                self._open_trade.get("atr_entry", 0),
                self._open_trade.get("max_runup_pct", 0),
                self._open_trade.get("max_drawdown_pct", 0),
                self._open_trade.get("ma_spread_entry", 0),
                "YES" if self._open_trade.get("tp1_hit", False) else "NO",
                "YES" if self._open_trade.get("tp2_hit", False) else "NO",
                self._open_trade.get("commission_usdt", 0),
            ])
        # ── Guardar en base de datos SQLite ──
        if self.database:
            self.database.save_trade({
                "trade_id": self._open_trade.get("trade_id", ""),
                "symbol": self._open_trade.get("symbol", ""),
                "side": self._open_trade.get("side", ""),
                "entry_price": float(self._open_trade.get("entry_price", 0)),
                "entry_time": self._open_trade.get("entry_time", ""),
                "exit_price": exit_price,
                "exit_time": exit_time,
                "qty": float(self._open_trade.get("initial_qty", self._open_trade.get("qty", 0))),
                "pnl_usdt": round(pnl_usdt, 4),
                "pnl_pct": round(pnl_pct, 4),
                "sl_initial": float(self._open_trade.get("sl_initial", 0)),
                "sl_final": float(sl_final) if sl_final else 0.0,
                "tp1": float(self._open_trade.get("tp1", 0)),
                "tp2": float(self._open_trade.get("tp2", 0)),
                "close_reason": close_reason,
                "duration_seconds": int(duration),
                "rsi_entry": float(self._open_trade.get("rsi_entry", 0)),
                "adx_entry": float(self._open_trade.get("adx_entry", 0)),
                "atr_entry": float(self._open_trade.get("atr_entry", 0)),
                "max_runup_pct": float(self._open_trade.get("max_runup_pct", 0)),
                "max_drawdown_pct": float(self._open_trade.get("max_drawdown_pct", 0)),
                "ma_spread_entry": float(self._open_trade.get("ma_spread_entry", 0)),
                "tp1_hit": self._open_trade.get("tp1_hit", False),
                "tp2_hit": self._open_trade.get("tp2_hit", False),
                "commission_usdt": float(self._open_trade.get("commission_usdt", 0)),
            })
        if self.audit:
            self.audit.log_trade(self._open_trade)
            
        if self.notifier:
            self.notifier.trade_close(
                symbol=self._open_trade["symbol"],
                side=self._open_trade["side"],
                entry=entry,
                exit_price=exit_price,
                pnl_usdt=pnl_usdt,
                pnl_pct=pnl_pct,
                reason=close_reason
            )

        logger.info(f"📝 Trade cerrado: {side} | PnL: {pnl_usdt:+.4f} USDT ({pnl_pct:+.2f}%) | Razón: {close_reason}")
        self._open_trade = {}