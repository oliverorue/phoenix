"""
Notificador: envía alertas enriquecidas a Telegram y/o Discord.
Incluye contexto de portfolio, P&L, VaR, Kelly, drawdown, etc.
"""

import requests
from datetime import datetime
from config.loader import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, DISCORD_WEBHOOK
from loguru import logger
import time
from typing import Any


class Notifier:
    """Envía mensajes enriquecidos a los canales configurados (Telegram, Discord)."""

    def __init__(self) -> None:
        self.telegram_token = TELEGRAM_TOKEN
        self.telegram_chat = TELEGRAM_CHAT_ID
        self.discord_webhook = DISCORD_WEBHOOK
        self.enabled = bool(self.telegram_token or self.discord_webhook)
        self._last_alert: dict[str, float] = {}   # tipo -> timestamp
        self.cooldown_seconds = {
            "trade_open": 60,
            "trade_close": 30,
            "error": 300,
            "daily_summary": 86400,
            "portfolio_alert": 600,
            "stop_limit": 120,
        }

        if not self.enabled:
            logger.warning("⚠️ Notificaciones deshabilitadas (sin token/webhook)")
        else:
            logger.info("✅ Notificaciones habilitadas")

    def send(self, title: str, message: str, severity: str = "INFO", alert_type: str = "generic") -> None:
        """Envía a todos los canales configurados respetando cooldown."""
        if not self.enabled:
            return

        # Respetar cooldown
        now = time.time()
        last = self._last_alert.get(alert_type, 0)
        cooldown = self.cooldown_seconds.get(alert_type, 10)
        if now - last < cooldown:
            logger.debug(f"Alerta '{alert_type}' en cooldown ({cooldown}s)")
            return

        self._last_alert[alert_type] = now

        emoji = {"CRITICAL": "🚨", "WARNING": "⚠️", "SUCCESS": "✅", "INFO": "ℹ️"}.get(severity, "📌")

        if self.telegram_token and self.telegram_chat:
            self._send_telegram(emoji, title, message)

        if self.discord_webhook:
            self._send_discord(emoji, title, message, severity)

    def _send_telegram(self, emoji: str, title: str, message: str) -> None:
        try:
            text = f"{emoji} <b>{title}</b>\n\n{message}\n\n<i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"
            requests.post(
                f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                json={"chat_id": self.telegram_chat, "text": text, "parse_mode": "HTML"},
                timeout=5,
            )
            logger.debug("✅ Telegram enviado")
        except Exception as e:
            logger.warning(f"⚠️ Error Telegram: {e}")

    def _send_discord(self, emoji: str, title: str, message: str, severity: str) -> None:
        try:
            colors = {"CRITICAL": 16711680, "WARNING": 16776960, "SUCCESS": 65280, "INFO": 3447003}
            requests.post(
                self.discord_webhook,
                json={
                    "embeds": [{
                        "title": f"{emoji} {title}",
                        "description": message,
                        "color": colors.get(severity, 3447003),
                        "timestamp": datetime.now().isoformat(),
                        "footer": {"text": "Trading Bot v4.0"}
                    }]
                },
                timeout=5,
            )
            logger.debug("✅ Discord enviado")
        except Exception as e:
            logger.warning(f"⚠️ Error Discord: {e}")

    # ── Helpers de contexto ──────────────────────────────────

    @staticmethod
    def _portfolio_context(bot: Any = None) -> str:
        """Construye bloque de contexto de portfolio para incluir en alertas."""
        if bot is None:
            return ""
        parts = []
        balance = getattr(bot, "balance", 0.0) or 0.0
        pr = getattr(bot, "portfolio_risk", None)
        ks = getattr(bot, "kelly_sizing", None)
        parts.append(f"💰 Balance: ${balance:.2f}")

        if pr is not None:
            try:
                parts.append(f"📊 Exp: ${pr.total_exposure:.2f} | Apal: {pr.effective_leverage:.2f}x")
            except Exception:
                pass

        if ks is not None:
            try:
                stats = ks.stats
                mult = stats.get("multiplier", 1.0) if isinstance(stats, dict) else 1.0
                parts.append(f"🎯 Kelly: {mult:.2f}x")
            except Exception:
                pass

        return " · ".join(parts)

    # ── Métodos específicos enriquecidos ──────────────────────

    def trade_open(self, symbol: str, side: str, entry: float, sl: float,
                   tp1: float, tp2: float, score: int,
                   bot: Any = None) -> None:
        """Notificación de apertura con contexto de portfolio."""
        emoji_side = "📈" if side == "LONG" else "📉"
        ctx = self._portfolio_context(bot)
        ctx_suffix = f"\n{ctx}" if ctx else ""
        msg = (
            f"Símbolo: {symbol}\n"
            f"Entrada: ${entry:.4f}\n"
            f"Stop Loss: ${sl:.4f}\n"
            f"Take Profit 1: ${tp1:.4f}\n"
            f"Take Profit 2: ${tp2:.4f}\n"
            f"Score: {score}/100\n"
            f"{ctx_suffix}"
        )
        self.send(f"{emoji_side} {side} ABIERTO", msg, "INFO", alert_type="trade_open")

    def trade_close(self, symbol: str, side: str, entry: float, exit_price: float,
                    pnl_usdt: float, pnl_pct: float, reason: str,
                    bot: Any = None) -> None:
        """Notificación de cierre con contexto extendido (PnL acumulado, win rate)."""
        emoji = "🟢" if pnl_usdt > 0 else "🔴"
        severity = "SUCCESS" if pnl_usdt > 0 else "WARNING"
        ctx = self._portfolio_context(bot)

        # Estadísticas adicionales desde DB si está disponible
        daily_pnl_str = ""
        if bot is not None:
            db = getattr(bot, "database", None)
            if db is not None:
                try:
                    today_stats = db.get_stats(days=1)
                    if today_stats:
                        dpnl = today_stats.get("total_pnl", 0) or 0
                        daily_pnl_str = f"\n📅 PnL diario: ${dpnl:+.2f}"
                except Exception:
                    pass

        ctx_suffix = f"\n\n{ctx}" if ctx else ""
        msg = (
            f"Símbolo: {symbol}\n"
            f"Entrada: ${entry:.4f} → Salida: ${exit_price:.4f}\n"
            f"PnL: {pnl_usdt:+.4f} USDT ({pnl_pct:+.2f}%)\n"
            f"Razón: {reason}"
            f"{daily_pnl_str}"
            f"{ctx_suffix}"
        )
        self.send(f"{emoji} {side} CERRADO", msg, severity, alert_type="trade_close")

    def error_alert(self, error_msg: str) -> None:
        self.send("❌ ERROR CRÍTICO", error_msg[:300], "CRITICAL", alert_type="error")

    def stop_limit(self, stops_today: int, max_stops: int, bot: Any = None) -> None:
        """Alerta de límite de stops con contexto."""
        ctx = self._portfolio_context(bot)
        msg = f"Stops hoy: {stops_today}/{max_stops}"
        if ctx:
            msg += f"\n\n{ctx}"
        self.send("🛑 LÍMITE DE STOPS", msg, "CRITICAL", alert_type="stop_limit")

    def portfolio_alert(self, title: str, message: str, bot: Any = None,
                        severity: str = "WARNING") -> None:
        """Alerta genérica de portfolio (VaR alto, drawdown, etc.)."""
        ctx = self._portfolio_context(bot)
        msg = message
        if ctx:
            msg += f"\n\n{ctx}"
        self.send(title, msg, severity, alert_type="portfolio_alert")

    def daily_summary(self, bot: Any = None) -> None:
        """Resumen diario completo enviado una vez al día."""
        if bot is None:
            return
        db = getattr(bot, "database", None)
        if db is None:
            return

        try:
            stats = db.get_stats(days=1)
            total = stats.get("total_trades", 0) or 0
            wins = stats.get("wins", 0) or 0
            losses = total - wins
            win_rate = (wins / total * 100) if total > 0 else 0.0
            total_pnl = stats.get("total_pnl", 0) or 0.0
            avg_pnl = stats.get("avg_pnl", 0) or 0.0

            balance = getattr(bot, "balance", 0.0) or 0.0
            pr = getattr(bot, "portfolio_risk", None)

            lines = [
                f"💰 Balance: ${balance:.2f}",
                f"📊 Trades hoy: {total} ({wins}W/{losses}L)",
                f"🎯 Win Rate: {win_rate:.1f}%",
                f"📈 PnL Diario: ${total_pnl:+.2f}",
                f"📉 PnL Promedio: ${avg_pnl:+.4f}",
            ]

            if pr is not None:
                try:
                    lines.append(f"📊 Exposición: ${pr.total_exposure:.2f}")
                    lines.append(f"⚡ Apalancamiento efectivo: {pr.effective_leverage:.2f}x")
                    var_data = pr.parametric_var()
                    if "error" not in var_data:
                        lines.append(f"🔴 VaR 95%: ${var_data.get('var_95', 0):.2f}")
                except Exception:
                    pass

            msg = "\n".join(lines)
            self.send("📊 RESUMEN DIARIO", msg, "INFO", alert_type="daily_summary")
        except Exception as e:
            logger.warning(f"Error generando resumen diario: {e}")
