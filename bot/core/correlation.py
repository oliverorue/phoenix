"""
Guard de correlación entre símbolos.

Previene la apertura simultánea de posiciones en símbolos que están
altamente correlacionados (ej: XRP + ETH = doble riesgo). Si un símbolo
de un grupo ya tiene posición abierta, los demás del mismo grupo quedan
bloqueados hasta que se cierre.

Configuración en settings.yaml:
    portfolio:
      correlation_groups:
        - name: "majors"
          symbols: ["XRPUSDT", "ETHUSDT"]
          max_concurrent: 1
"""

from typing import Any, Callable, Optional
from loguru import logger


class CorrelationGuard:
    """
    Guard que impide abrir posiciones en símbolos correlacionados
    cuando ya hay una posición activa en el mismo grupo.

    Uso:
        guard = CorrelationGuard(groups=config)
        guard.bind_runners(runners_list)

        # Antes de abrir posición:
        if not guard.can_open("XRPUSDT"):
            logger.info("XRPUSDT bloqueado por correlación con ETHUSDT")
            return
    """

    def __init__(self, groups: Optional[list[dict[str, Any]]] = None) -> None:
        """
        Args:
            groups: Lista de dicts con:
                - name: str — nombre del grupo (ej: "majors")
                - symbols: list[str] — símbolos del grupo
                - max_concurrent: int — máx posiciones simultáneas (default 1)
        """
        self._groups: list[dict[str, Any]] = groups or []
        # Mapa símbolo → info del grupo
        self._symbol_map: dict[str, dict[str, Any]] = {}
        # Mapa símbolo → función para verificar si tiene posición abierta
        self._position_getters: dict[str, Callable[[], bool]] = {}
        self._build_symbol_map()

    def _build_symbol_map(self) -> None:
        """Construye mapa inverso símbolo → grupo."""
        self._symbol_map.clear()
        for group in self._groups:
            for sym in group.get("symbols", []):
                self._symbol_map[sym] = group

    def bind_runners(self, runners: list[Any]) -> None:
        """
        Vincula los runners para poder consultar su estado.
        Almacena una referencia a runner.executor.position_open.
        """
        self._position_getters.clear()
        for r in runners:
            self._position_getters[r.symbol] = (
                lambda ex=r.executor: ex.position_open
            )

    def bind_symbol(self, symbol: str, getter: Callable[[], bool]) -> None:
        """
        Vincula un símbolo individual con un callable que retorna
        True si tiene posición abierta.
        """
        self._position_getters[symbol] = getter

    def can_open(self, symbol: str) -> bool:
        """
        Verifica si se puede abrir posición en `symbol` sin exceder
        el límite de concurrentes en su grupo de correlación.

        Revisa:
        1. Si el símbolo MISMO ya tiene posición abierta
        2. Si otros símbolos del mismo grupo ya tienen posición

        Returns:
            True si se permite abrir, False si está bloqueado.
        """
        group = self._symbol_map.get(symbol)
        if not group:
            # Sin grupo definido = sin restricción
            return True

        max_concurrent = group.get("max_concurrent", 1)
        count = 0

        for sym in group["symbols"]:
            getter = self._position_getters.get(sym)
            if getter and getter():
                if sym == symbol:
                    # El propio símbolo ya tiene posición abierta
                    logger.info(
                        f"🔒 Correlación: {symbol} ya tiene posición abierta"
                    )
                    return False
                count += 1
                if count >= max_concurrent:
                    logger.info(
                        f"🔒 Correlación: {symbol} bloqueado — "
                        f"ya hay posición en {sym} (grupo {group['name']})"
                    )
                    return False

        return True

    def get_active_positions(self) -> list[dict[str, Any]]:
        """
        Retorna lista de posiciones activas agrupadas por grupo de correlación.
        Útil para dashboard/logging.
        """
        result: list[dict[str, Any]] = []
        for group in self._groups:
            active = [
                sym for sym in group.get("symbols", [])
                if self._position_getters.get(sym, lambda: False)()
            ]
            if active:
                result.append({
                    "group": group["name"],
                    "symbols": active,
                    "count": len(active),
                    "max_concurrent": group.get("max_concurrent", 1),
                })
        return result

    @property
    def stats(self) -> dict[str, Any]:
        """Estadísticas de correlación."""
        groups = []
        for g in self._groups:
            active = 0
            for sym in g.get("symbols", []):
                getter = self._position_getters.get(sym)
                if getter and getter():
                    active += 1
            groups.append({
                "name": g["name"],
                "symbols": g["symbols"],
                "active": active,
                "max_concurrent": g.get("max_concurrent", 1),
                "blocked": active >= g.get("max_concurrent", 1),
            })
        return {
            "groups": groups,
            "total_groups": len(self._groups),
        }

    @property
    def blocked_symbols(self) -> list[str]:
        """
        Retorna lista de símbolos actualmente bloqueados por correlación.
        """
        blocked: list[str] = []
        for group in self._groups:
            max_c = group.get("max_concurrent", 1)
            active = [
                sym for sym in group.get("symbols", [])
                if self._position_getters.get(sym, lambda: False)()
            ]
            if len(active) >= max_c:
                # Todos los demás símbolos del grupo están bloqueados
                for sym in group.get("symbols", []):
                    if sym not in active:
                        blocked.append(sym)
        return blocked
