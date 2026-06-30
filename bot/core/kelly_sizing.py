"""
Kelly Criterion Dynamic Sizing.

Ajusta el riesgo (`risk_per_trade`) dinámicamente basado en:
- **Kelly Criterion** sobre una ventana deslizante de trades recientes
- **Racha** de wins/losses consecutivos (modulación adicional)

Uso:
    kelly = KellySizing(window_size=20, min_trades=5)
    kelly.record_trade(12.5)   # PnL positivo (win)
    kelly.record_trade(-4.2)   # PnL negativo (loss)
    mult = kelly.get_multiplier()  # → 1.35 (aumentar riesgo 35%)
"""

from typing import Any


class KellySizing:
    """
    Calcula un multiplicador dinámico para `risk_per_trade` usando
    Kelly Criterion + modulación por racha.

    El multiplicador se aplica sobre el `risk_per_trade` base de la fase:
        risk_efectivo = risk_per_trade * kelly_multiplier

    Fix #2: max_multiplier reducido + conservative_tier: si hay menos de
    `conservative_tier_threshold` trades, se usa un techo más bajo.
    Fix #8: streak_boost reducido con cap absoluto `max_streak_boost`.

    Attributes:
        window_size:  Ventana de trades recientes para calcular win-rate y avg W/L.
        min_trades:   Mínimo de trades antes de empezar a ajustar (devuelve 1.0).
        max_multiplier: Techo del multiplicador (default 1.5).
        min_multiplier: Piso del multiplicador (default 0.3).
        fractional:   Fracción del Kelly completo a usar (default 0.25 = 25%).
        streak_boost:  Aumento adicional por win consecutivo (default 0.04).
        streak_penalty: Reducción por loss consecutivo (default 0.08).
        max_streak_boost: Cap absoluto del boost por racha (default 0.20).
        conservative_tier_threshold: Mínimo de trades para aplicar max_multiplier completo.
        conservative_max_multiplier: Techo conservador para trades iniciales.
    """

    def __init__(
        self,
        window_size: int = 20,
        min_trades: int = 5,
        max_multiplier: float = 1.5,
        min_multiplier: float = 0.3,
        fractional: float = 0.25,
        streak_boost: float = 0.04,
        streak_penalty: float = 0.08,
        max_streak_boost: float = 0.20,
        conservative_tier_threshold: int = 50,
        conservative_max_multiplier: float = 1.2,
    ) -> None:
        if window_size < 2:
            raise ValueError("window_size debe ser >= 2")
        if min_trades < 1:
            raise ValueError("min_trades debe ser >= 1")
        if fractional <= 0 or fractional > 1.0:
            raise ValueError("fractional debe estar en (0, 1]")
        if max_multiplier < min_multiplier:
            raise ValueError("max_multiplier debe ser >= min_multiplier")
        if max_streak_boost <= 0:
            raise ValueError("max_streak_boost debe ser > 0")

        self.window_size = window_size
        self.min_trades = min_trades
        self.max_multiplier = max_multiplier
        self.min_multiplier = min_multiplier
        self.fractional = fractional
        self.streak_boost = streak_boost
        self.streak_penalty = streak_penalty
        self.max_streak_boost = max_streak_boost
        self.conservative_tier_threshold = conservative_tier_threshold
        self.conservative_max_multiplier = conservative_max_multiplier

        # Estado interno
        self._trades: list[float] = []       # PnL de todos los trades (orden cronológico)
        self._streak: int = 0                # >0 = wins consecutivos, <0 = losses consecutivos

    # ── API pública ─────────────────────────────────────────────

    def record_trade(self, pnl_usdt: float) -> None:
        """
        Registra un trade cerrado.

        Args:
            pnl_usdt: PnL en USDT del trade (positivo = win, negativo = loss).
        """
        self._trades.append(pnl_usdt)

        if pnl_usdt > 0:
            self._streak = self._streak + 1 if self._streak >= 0 else 1
        elif pnl_usdt < 0:
            self._streak = self._streak - 1 if self._streak <= 0 else -1
        else:
            self._streak = 0  # breakeven no afecta racha

    def get_multiplier(self) -> float:
        """
        Retorna el multiplicador a aplicar sobre `risk_per_trade`.

        Fix #2: Aplica un techo conservador si hay menos de
        `conservative_tier_threshold` trades (modo bootstrap).

        - Si aún no hay suficientes trades → 1.0 (sin ajuste)
        - Si todos son wins → effective_max_multiplier
        - Si todos son losses → min_multiplier
        - Si hay mix → Kelly Criterion + modulación por racha
        """
        if len(self._trades) < self.min_trades:
            return 1.0

        # Fix #2: Techo conservador para los primeros N trades (bootstrap)
        if len(self._trades) < self.conservative_tier_threshold:
            effective_max = min(self.max_multiplier, self.conservative_max_multiplier)
        else:
            effective_max = self.max_multiplier

        # Ventana deslizante
        window = self._trades[-self.window_size:]
        wins = [t for t in window if t > 0]
        losses = [t for t in window if t < 0]
        n = len(window)

        # Casos extremos
        if not losses:
            return effective_max
        if not wins:
            return self.min_multiplier

        # Métricas
        win_rate = len(wins) / n
        avg_win = sum(wins) / len(wins)
        avg_loss = abs(sum(losses) / len(losses))
        b = avg_win / avg_loss if avg_loss > 0 else 1.0

        # ── Kelly Criterion ──
        # f* = (p * b - q) / b  =  p - (1-p)/b
        # Positivo → estrategia con ventaja; Negativo → desventaja
        kelly_f = (win_rate * b - (1 - win_rate)) / b

        # Aplicar fraccional de Kelly (conservador)
        kelly_f = kelly_f * self.fractional

        # ── Mapear edge → multiplier ──
        # edge = 0  → multiplier = 1.0  (sin ajuste)
        # edge > 0  → multiplier > 1.0  (aumentar riesgo, hasta effective_max)
        # edge < 0  → multiplier < 1.0  (reducir riesgo, hasta min_mult)
        # edge_max es el valor de edge que consideramos "señal completa"
        edge_max = 0.15
        if kelly_f >= 0:
            t = min(kelly_f / edge_max, 1.0)
            base_mult = 1.0 + t * (effective_max - 1.0)
        else:
            t = min(abs(kelly_f) / edge_max, 1.0)
            base_mult = 1.0 - t * (1.0 - self.min_multiplier)

        # ── Modulación por racha ──
        # Fix #8: streak_boost reducido (0.04) con cap absoluto max_streak_boost (0.20)
        streak_mod = 1.0
        if self._streak >= 3:
            # 3+ wins seguidos: hasta +max_streak_boost extra (antes era +50%)
            streak_mod = 1.0 + min(self._streak * self.streak_boost, self.max_streak_boost)
        elif self._streak <= -2:
            # 2+ losses seguidos: hasta -50% extra (penalización asimétrica más agresiva)
            streak_mod = 1.0 + max(self._streak * self.streak_penalty, -0.5)

        multiplier = base_mult * streak_mod
        return max(self.min_multiplier, min(multiplier, effective_max))

    # ── Propiedades de consulta ─────────────────────────────────

    @property
    def streak(self) -> int:
        """Racha actual: positivo = wins, negativo = losses."""
        return self._streak

    @property
    def total_trades(self) -> int:
        """Total de trades registrados (toda la historia)."""
        return len(self._trades)

    @property
    def stats(self) -> dict[str, Any]:
        """Métricas calculadas sobre la ventana deslizante."""
        if len(self._trades) < self.min_trades:
            return {
                "total_trades": len(self._trades),
                "streak": self._streak,
                "status": "insufficient_data",
                "multiplier": 1.0,
            }

        window = self._trades[-self.window_size:]
        wins = [t for t in window if t > 0]
        losses = [t for t in window if t < 0]

        win_rate = len(wins) / len(window) if window else 0.0
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
        total_pnl = sum(window)

        # Calcular profit factor con la ventana actual
        total_wins = sum(wins) if wins else 0.0
        total_losses = abs(sum(losses)) if losses else 1.0
        profit_factor = total_wins / total_losses if total_losses > 0 else float("inf")

        return {
            "total_trades": len(self._trades),
            "window_trades": len(window),
            "streak": self._streak,
            "win_rate": round(win_rate, 4),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "win_loss_ratio": round(avg_win / avg_loss, 4) if avg_loss > 0 else float("inf"),
            "profit_factor": round(profit_factor, 4),
            "total_pnl_window": round(total_pnl, 4),
            "multiplier": round(self.get_multiplier(), 4),
            "status": "active",
        }
