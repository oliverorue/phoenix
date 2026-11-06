---
description: Revisa OrderExecutor, PositionManager y señales con foco en riesgo y latencia
agent: quant
subtask: true
---

# Comando: review-trade

Analizá el código indicado en `$ARGUMENTS` (o, si no se indicó nada, `bot/core/execution.py`, `bot/core/position_manager.py`, `bot/core/paper_executor.py` y `bot/core/symbol_runner.py`).

## Checklist obligatorio

1. **Bloqueos en async**
   - Detectar llamadas HTTP/DB síncronas dentro de coroutines.
   - Verificar que `python-binance` se use con `await asyncio.to_thread(...)`.
   - Detectar `time.sleep()` en async.

2. **Protección de la posición**
   - Confirmar que SL y TP se envían en batch o inmediatamente después de la orden principal.
   - Verificar `reduceOnly` en cierres y reducciones.
   - Verificar que el cierre usa la cantidad remanente, no la original, después de TP1.
   - Verificar que TP1 se marca como ejecutado solo con fill confirmado.
   - Verificar que el trailing stop se mueve en la dirección correcta.

3. **Correctitud Binance**
   - Símbolo correcto en `get_instrument_info()` (nunca `symbols[0]`).
   - Filtros `LOT_SIZE`, `PRICE_FILTER`, `NOTIONAL`.
   - `stepSize`, `minQty`, `maxQty`, precisión de precio y mínimo nocional.
   - Intervalos normalizados (`"15" → "15m"`, `"60" → "1h"`).
   - Precio/cantidad reales del fill.

4. **Riesgo**
   - Balance correcto en live/paper (`_backtest_balance` solo en backtest).
   - `capital_inicio_dia`, stops diarios y pérdidas consecutivas.
   - Cooldown por vela cerrada en vivo.
   - `dias_excluir` aplicado en vivo.
   - Exposición total con el nocional de la nueva posición.

5. **Paridad paper/backtest/live**
   - Mismas comisiones, slippage y parciales.
   - Sin look-ahead.
   - Misma prioridad SL/TP dentro de la vela.

6. **Dependencias**
   - No introducir `ccxt` ni PostgreSQL/Redis en un fix.
   - Si proponés migrar a `ccxt.async_support`, presentalo como proyecto separado con plan, tests y comparación.

## Salida esperada

- Lista de hallazgos con `archivo:línea`, severidad (`CRÍTICO`/`ALTO`/`MEDIO`/`BAJO`) e impacto.
- Parche mínimo propuesto.
- Tests de regresión a agregar.
- Comandos de validación a ejecutar.

No apliques cambios sin mostrar el plan cuando el fix toque riesgo, sizing, TP/SL o recuperación de posiciones.
