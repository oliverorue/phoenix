"""
Portfolio Risk Management — VaR y Exposición Total.

Calcula en tiempo real:
- **Exposición total**: suma de nocionales absolutos de todas las posiciones
- **Exposición neta**: suma de nocionales con signo (LONG +, SHORT -)
- **VaR paramétrico**: basado en desviación estándar de PnL histórico
- **VaR por posición**: desglose individual
- **Límite de exposición**: detiene nuevas entradas si se supera

Integración:
    risk = PortfolioRisk(database=db, notifier=notifier)
    risk.bind_runners(runners)

    # En run_cycle, antes de abrir posición:
    if not risk.can_open():
        logger.warning("Exposición máxima alcanzada")
        return

    # En dashboard:
    state["portfolio_risk"] = risk.stats
"""

from typing import Any, Optional
from loguru import logger


class PortfolioRisk:
    """
    Monitorea y calcula el riesgo agregado del portfolio.

    Attributes:
        var_confidence: Nivel de confianza para VaR (default 0.95).
        var_lookback_days: Ventana histórica para estimar volatilidad (default 30).
        max_exposure_usdt: Límite de exposición total en USDT (0 = sin límite).
        max_exposure_pct: Límite de exposición como % del balance (0 = sin límite).
    """

    def __init__(
        self,
        database: Any = None,
        notifier: Any = None,
        var_confidence: float = 0.95,
        var_lookback_days: int = 30,
        max_exposure_usdt: float = 0.0,
        max_exposure_pct: float = 0.0,
    ) -> None:
        self.database = database
        self.notifier = notifier
        self.var_confidence = var_confidence
        self.var_lookback_days = var_lookback_days
        self.max_exposure_usdt = max_exposure_usdt
        self.max_exposure_pct = max_exposure_pct

        self._runners: list[Any] = []
        # Cache de VaR para no recalcular en cada ciclo
        self._cached_var: Optional[dict[str, Any]] = None
        self._cache_expiry: float = 0.0

    # ── Vinculación ───────────────────────────────────────────

    def bind_runners(self, runners: list[Any]) -> None:
        """Vincula los runners para leer posiciones activas."""
        self._runners = runners

    # ── Datos de posiciones ───────────────────────────────────

    def _position_data(self) -> list[dict[str, Any]]:
        """
        Retorna datos mark-to-market de todas las posiciones abiertas.

        Usa `executor.last_price` como precio actual (actualizado en run_cycle).
        """
        positions: list[dict[str, Any]] = []
        for r in self._runners:
            ex = r.executor
            if ex.position_open:
                # Nocional al entry y al precio actual (mark-to-market)
                notional = ex.entry_price * ex.quantity
                current_price = ex.last_price if ex.last_price > 0 else ex.entry_price
                current_notional = current_price * ex.quantity

                # PnL no realizado
                if ex.position_type == "LONG":
                    unrealized_pnl = current_notional - notional
                else:
                    unrealized_pnl = notional - current_notional

                positions.append({
                    "symbol": r.symbol,
                    "side": ex.position_type,
                    "qty": ex.quantity,
                    "entry_price": ex.entry_price,
                    "current_price": current_price,
                    "notional": round(notional, 4),
                    "current_notional": round(current_notional, 4),
                    "sl": ex.current_sl,
                    "unrealized_pnl": round(unrealized_pnl, 4),
                })
        return positions

    # ── Propiedades de exposición ─────────────────────────────

    @property
    def total_exposure(self) -> float:
        """Suma de nocionales absolutos (mark-to-market)."""
        return sum(p["current_notional"] for p in self._position_data())

    @property
    def net_exposure(self) -> float:
        """Suma de nocionales con signo (LONG +, SHORT -)."""
        total = 0.0
        for p in self._position_data():
            sign = 1 if p["side"] == "LONG" else -1
            total += sign * p["current_notional"]
        return total

    @property
    def num_positions(self) -> int:
        """Cantidad de posiciones abiertas actualmente."""
        return sum(1 for r in self._runners if r.executor.position_open)

    @property
    def active_positions(self) -> list[dict[str, Any]]:
        """Lista detallada de posiciones abiertas."""
        return self._position_data()

    # ── VaR (Value at Risk) ───────────────────────────────────

    def parametric_var(self) -> dict[str, Any]:
        """
        VaR paramétrico usando desviación estándar de PnL histórico.

        Fix #7: Mínimo de 20 trades requeridos para que el VaR sea confiable.
        Con menos de 20 muestras, la desviación estándar no es estadísticamente
        significativa. Se usa 20 como umbral conservador (regla empírica para
        aproximación normal).

        Método:
            1. Obtiene últimos N trades cerrados desde la DB
            2. Calcula σ de los retornos (pnl_pct)
            3. VaR = z × σ × exposure_actual

        Returns:
            Dict con VaR 95%, VaR 99%, σ, samples usados.
            Si no hay suficientes datos, retorna error con samples=0.
        """
        if not self.database:
            return {"error": "No database disponible", "samples": 0}

        trades = self.database.get_trades(limit=self.var_lookback_days * 3)
        # Retornos con signo, expresados como fracción (no como porcentaje).
        returns = [
            float(t.get("pnl_pct", 0)) / 100.0 for t in trades
            if t.get("pnl_pct", 0) != 0
        ]

        # Fix #7: Aumentar umbral mínimo de 5 a 20 para confiabilidad estadística
        MIN_VAR_SAMPLES = 20
        if len(returns) < MIN_VAR_SAMPLES:
            return {
                "error": f"Datos insuficientes: {len(returns)} trades (mínimo {MIN_VAR_SAMPLES})",
                "samples": len(returns),
            }

        import statistics
        sigma_dec = statistics.stdev(returns) if len(returns) > 1 else 0.0
        sigma_pct = sigma_dec * 100.0

        exposure = self.total_exposure
        if exposure <= 0:
            exposure = 1.0  # evitar división por cero

        z_95 = 1.645
        z_99 = 2.326

        var_95 = z_95 * sigma_dec * exposure
        var_99 = z_99 * sigma_dec * exposure

        result = {
            "sigma_pct": round(sigma_pct, 4),
            "exposure": round(self.total_exposure, 2),
            "var_95": round(var_95, 2),
            "var_99": round(var_99, 2),
            "var_95_pct": round(var_95 / exposure * 100, 2),
            "var_99_pct": round(var_99 / exposure * 100, 2),
            "samples": len(returns),
            "confidence": self.var_confidence,
        }
        return result

    # ── Límite de exposición ──────────────────────────────────

    def can_open(self, notional_to_add: float = 0.0) -> bool:
        """
        Verifica si se puede abrir una nueva posición sin exceder límites.

        Fix #7: Integra VaR como criterio adicional de bloqueo.
        Si el VaR 95% supera el 10% de la exposición actual, se bloquean
        nuevas entradas (riesgo de cola demasiado alto).

        Args:
            notional_to_add: Nocional estimado de la nueva posición.

        Returns:
            True si está dentro de límites, False si se excede.
        """
        # Sin límite configurado
        if self.max_exposure_usdt <= 0 and self.max_exposure_pct <= 0:
            # Aun sin límites de exposición, verificar VaR si hay datos
            return self._can_open_var_check()

        current_exp = self.total_exposure
        new_exp = current_exp + abs(notional_to_add)

        # Límite absoluto en USDT
        if self.max_exposure_usdt > 0 and new_exp > self.max_exposure_usdt:
            logger.warning(
                f"🚫 Exposición máxima ${self.max_exposure_usdt:.0f} "
                f"alcanzada: ${new_exp:.0f}"
            )
            return False

        # Límite como % del balance
        if self.max_exposure_pct > 0 and self._runners:
            try:
                balance = self._runners[0].balance
                if balance > 0:
                    exp_pct = new_exp / balance * 100
                    if exp_pct > self.max_exposure_pct:
                        logger.warning(
                            f"🚫 Exposición máxima {self.max_exposure_pct:.0f}% "
                            f"del balance: {exp_pct:.0f}% (${new_exp:.0f})"
                        )
                        return False
            except Exception:
                pass

        # Fix #7: Verificar VaR incluso si los límites de exposición pasan
        return self._can_open_var_check()

    def _can_open_var_check(self) -> bool:
        """
        Fix #7: Verificación independiente de VaR.

        Si el VaR 95% supera el 10% de la exposición total, se bloquean
        nuevas entradas. Esto previene operar cuando la volatilidad histórica
        indica que el riesgo de cola es demasiado alto para el portfolio actual.

        Returns:
            True si el VaR está dentro de límites aceptables.
        """
        var_data = self.parametric_var()
        if "error" not in var_data:
            var_95_pct = var_data.get("var_95_pct", 0)
            # Si VaR 95% > 10% de la exposición, es señal de alta volatilidad
            VAR_EXPOSURE_THRESHOLD_PCT = 10.0
            if var_95_pct > VAR_EXPOSURE_THRESHOLD_PCT:
                logger.warning(
                    f"🚫 VaR 95% demasiado alto: {var_95_pct:.1f}% de la exposición "
                    f"(límite: {VAR_EXPOSURE_THRESHOLD_PCT:.0f}%)"
                )
                return False
        # Si no hay datos suficientes de VaR (menos de 20 trades),
        # no bloquear — solo usar límites de exposición porcentual
        return True

    # ── Apalancamiento efectivo ───────────────────────────────

    @property
    def effective_leverage(self) -> float:
        """
        Apalancamiento efectivo del portfolio: exposure / balance.
        """
        balance = 100.0
        if self._runners:
            try:
                balance = self._runners[0].balance
            except Exception:
                pass
        exp = self.total_exposure
        return round(exp / balance, 2) if balance > 0 else 0.0

    # ── Stats completo ────────────────────────────────────────

    @property
    def stats(self) -> dict[str, Any]:
        """Resumen completo de riesgo del portfolio."""
        positions = self._position_data()
        var_data = self.parametric_var()

        return {
            "num_positions": len(positions),
            "total_exposure": round(self.total_exposure, 2),
            "net_exposure": round(self.net_exposure, 2),
            "effective_leverage": self.effective_leverage,
            "positions": positions,
            "var": var_data,
            "limits": {
                "max_exposure_usdt": self.max_exposure_usdt,
                "max_exposure_pct": self.max_exposure_pct,
                "can_open": self.can_open(),
            },
        }
