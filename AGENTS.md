# AGENTS.md — Phoenix v5.1

> **Modo actual: monitor-only.** Este build **NO ejecuta órdenes**. El único
> proceso operativo es el monitor de señales; el trading paper/live está
> deshabilitado. No reactivar la ejecución sin pedido explícito del usuario.

Este archivo es la referencia obligatoria para cualquier agente que trabaje en este repositorio. Antes de tocar código, leerlo y respetar sus invariantes.

## Contexto del proyecto

Phoenix es un **monitor de señales** para **Binance USDⓈ-M Futures**. Muestra entradas, SL/TP, tamaño sugerido y contexto de riesgo; no envía órdenes. El backtester y la estrategia siguen disponibles para validación. Incluye:

- Dimensionamiento dinámico con Criterio de Kelly.
- VaR paramétrico y límites de exposición/correlación.
- Gestión de fases por balance, drawdown y stops diarios.
- Journal de trades, auditoría local y exportación de métricas a Prometheus.
- Dashboard de monitoreo en Streamlit.

El objetivo inmediato **no es aumentar rentabilidad**: es hacer que datos, riesgo y backtest sean correctos y reproducibles. Respetar el plan `plans/improvement_plan_2026-11.md`.

## Modo monitor-only (invariante actual)

- `monitor_only: true` en `config/settings.yaml` → `MONITOR_ONLY` en `config/loader.py`.
- Entrypoint operativo: `python3 -m bot.monitor` (o `python3 bot/monitor.py`).
- `python3 -m bot.main` delega al monitor; `TradingBot.run()` exige `allow_trading=True`.
- El dashboard no puede iniciar/detener el bot (`BotController.control_allowed()` = `False`).
- Systemd, Docker y los scripts de arranque ejecutan `bot.monitor`.
- No reintroducir ejecución de órdenes, API keys privadas ni `PaperExecutor` en el camino operativo.

## Stack real (no inventar tecnologías)

- **Python**: `>=3.12` (`pyproject.toml`). CI prueba 3.12–3.14.
- **Exchange**: `python-binance` (cliente REST/WS actual). **No hay `ccxt` en el proyecto.**
- **Async**: `asyncio`, `websockets`.
- **Datos**: `pandas`, `numpy`.
- **Config/validación**: `pydantic` v2, `PyYAML`, `python-dotenv`.
- **Logging**: `loguru`.
- **Dashboard**: `streamlit`, `plotly`.
- **Observabilidad**: `prometheus_client`, OpenTelemetry (opcional; hoy deshabilitado por defecto).
- **Persistencia actual**: SQLite (`data/bot_history.db`) + JSON de estado.
- **Objetivo futuro, NO implementado**: PostgreSQL/TimescaleDB y Redis. No introducirlos sin un plan explícito y aprobación.

No agregar dependencias nuevas sin justificar que el stack actual no resuelve el problema.

## Reglas no negociables

### 1. Seguridad de ejecución

- `exchange.testnet` de `config/settings.yaml` es la fuente principal del modo. Si `TESTNET` está definido en el entorno, **debe coincidir**; si no, el bot debe abortar.
- Mainnet real exige `LIVE_TRADING_CONFIRMED=true` y `paper_trading.enabled=false`. No eliminar ni relajar este gate.
- Nunca asumir un balance por defecto (por ejemplo `$100`). Sin balance real, el bot no arranca.
- Nunca hardcodear API keys, tokens ni secretos. Usar `config/secrets.env` / variables de entorno.
- No cambiar el modo testnet/mainnet en caliente. Requiere reinicio.
- No iniciar un segundo bot desde el dashboard. En Docker, `PHOENIX_DASHBOARD_ALLOW_BOT_CONTROL=false`.

### 2. Asincronía y latencia

- Ninguna llamada HTTP/DB síncrona debe ejecutarse directamente dentro de una coroutine.
- `python-binance` es síncrono: envolver las llamadas con `await asyncio.to_thread(...)` o migrar a un cliente async **como proyecto separado**.
- No usar `time.sleep()` dentro de código async. Usar `asyncio.sleep()`.
- Toda llamada de red debe manejar:
  - timeouts,
  - `429` / rate limit,
  - desconexiones de WebSocket,
  - reconexión con backoff exponencial.
- Evitar bucles Python sobre datasets grandes. Vectorizar con `numpy`/`pandas` cuando aporte claridad y rendimiento.
- No recalcular indicadores completos en cada tick si alcanza con deltas o caché; pero no optimizar prematuramente sin medir.

### 3. Correctitud contra Binance

- Los intervalos deben normalizarse en un solo lugar: `"15" → "15m"`, `"60" → "1h"`. Nunca enviar `"15"` o `"60"` a la API.
- `get_instrument_info(symbol)` debe buscar **ese** símbolo. Nunca usar `symbols[0]`.
- Leer los filtros correctos: `LOT_SIZE`, `PRICE_FILTER`, `NOTIONAL` (no solo `MIN_NOTIONAL`).
- Respetar `stepSize`, `minQty`, `maxQty`, precisión de precio y mínimo nocional.
- Los cierres y reducciones deben usar `reduceOnly` cuando corresponda.
- Guardar el precio/cantidad reales del fill (`avgPrice`, `executedQty`), no el precio teórico de la señal.
- Usar `newClientOrderId` único para evitar duplicados tras timeout/reintento.

### 4. Invariantes de riesgo

- En live/paper, el `RiskManager` debe usar el balance real o el del `PaperExecutor`. `_backtest_balance` es **solo** para backtest.
- No usar `set_balance()` como si fuera “balance inicial live”: congela el balance y desactiva drawdown/fases.
- `capital_inicio_dia` debe reiniciarse al cambiar de día.
- `stops_dia` cuenta solo stops reales (SL/pérdida), no TP, cierres manuales ni reversiones.
- Las pérdidas consecutivas se resetean con una ganancia.
- `dias_excluir` debe aplicarse también en vivo.
- El cooldown debe avanzar por vela cerrada en vivo, no por tick de 5 segundos.
- `PortfolioRisk.can_open()` debe recibir el nocional estimado de la nueva posición.
- No cambiar fórmulas de Kelly/VaR sin tests numéricos y comparación paper/backtest.

### 5. Posiciones, TP y SL

- TP1 es parcial: cerrar `tp1_porcentaje` (65% por defecto) y reducir la cantidad restante en el estado.
- El cierre posterior debe usar la cantidad remanente, nunca la original completa.
- No marcar `tp1_hit` solo porque el precio tocó TP1: confirmar el fill.
- SL y TP deben enviarse en batch o inmediatamente después de la orden principal.
- El trailing stop debe moverse en la dirección correcta y actualizarse periódicamente.
- Tras un reinicio, reponer SL y TPs faltantes. No cancelar el SL y dejarlo sin recolocar.
- No sobrescribir el estado del exchange con un JSON viejo sin validarlo.

### 6. Paridad paper / backtest / live

Los tres motores deben compartir la misma semántica:

- mismas comisiones y slippage,
- mismos parciales TP1/TP2,
- misma prioridad SL/TP dentro de una vela (documentar el criterio conservador),
- mismos filtros de sesión, cooldown y riesgo,
- sin look-ahead en backtest.

Cualquier cambio de comportamiento debe reflejarse en los tres o quedar explícitamente documentado como divergencia.

### 7. Precisión numérica

- Usar `Decimal` en los bordes financieros: balances, fees, precios/quantities enviados al exchange y persistencia contable.
- Mantener `float` en indicadores y cálculos vectorizados con `pandas`/`numpy`.
- No mezclar `Decimal` con arrays de `numpy`/`pandas`. Convertir en el límite.
- No redondear cantidades “a ojo”: usar `stepSize` y precisión del símbolo.

### 8. Testing

- Todo fix entra con test de regresión.
- El suite por defecto **no toca red**: `pytest -m "not integration"`.
- Los tests de red/Testnet van marcados `@pytest.mark.integration` y son opt-in.
- Mockear Binance/DB; no usar sleeps largos ni datos aleatorios no deterministas.
- Para lógica financiera, usar datasets sintéticos con resultado conocido.
- Antes de terminar:
  ```bash
  pytest -m "not integration"
  flake8 bot/ --select=E9,F63,F7,F82
  ```
- `mypy` todavía tiene deuda (Fase 7). No aumentar la cantidad de errores.

### 9. Configuración y código

- Una sola fuente de verdad para configuración. Respetar `config/loader.py` y `config/settings.yaml`.
- No reintroducir referencias a Bybit ni versiones 4.x.
- Mantener type hints y el estilo existente.
- Evitar refactors masivos mezclados con fixes. Un cambio = un objetivo.
- No agregar comentarios obvios; documentar decisiones no evidentes.
- No commitear secretos, `.env`, bases de datos ni logs.

## Mapa de arquitectura

| Área | Archivos clave |
|---|---|
| Orquestación | `bot/main.py`, `bot/watchdog.py` |
| Ciclo por símbolo | `bot/core/symbol_runner.py` |
| Datos/exchange | `bot/core/data_feed.py`, `exchange_client.py`, `ws_feed.py`, `market_data.py` |
| Estrategia | `bot/core/strategy_engine.py`, `indicators.py` |
| Riesgo | `bot/core/risk_manager.py`, `portfolio_risk.py`, `kelly_sizing.py`, `correlation.py` |
| Ejecución | `bot/core/execution.py`, `paper_executor.py`, `position_manager.py` |
| Persistencia | `bot/core/trade_journal.py`, `database.py`, `state_recovery.py`, `exchange_recovery.py` |
| Config | `config/loader.py`, `config/settings.yaml`, `config/secrets.env` |
| Dashboard | `bot/dashboard/app.py`, `bot_controller.py`, `shared_state.py` |
| Backtest | `bot/core/backtester.py`, `optimizer.py`, `stress_test.py` |
| Scripts | `scripts/` |
| Tests | `tests/` |

## Deuda crítica conocida (no reintroducir)

Estos son problemas reales detectados en la auditoría. Si trabajás en el área, corregilos; no los copies a código nuevo:

- Intervalos `"15"`/`"60"` enviados a Binance (`data_feed.py`, `exchange_client.py`).
- `get_instrument_info()` usando `symbols[0]`.
- `RiskManager` con balance congelado por `set_balance()` en `SymbolRunner`.
- Stops contados en cualquier cierre y pérdidas consecutivas sin reset por ganancia.
- Cooldown que no avanza en vivo.
- Paper trading sin SL/TP2/parciales.
- Cierre con cantidad completa aunque TP1 ya ejecutó parcial.
- Trailing stop con precio de activación del lado equivocado.
- Shutdown que busca `runner.position` (no existe) y no cierra posiciones.
- Backtest que pagina una vela por request y congela la ventana 1h.
- Journal único compartido entre símbolos y PnL sin comisiones.
- Recuperación que cancela el SL y no lo repone.

## Protocolo de cambio

1. Leer los archivos afectados y el plan de mejora.
2. Identificar el invariante que el cambio puede romper.
3. Hacer el cambio más pequeño posible.
4. Agregar/actualizar tests.
5. Correr `pytest -m "not integration"` y el gate de flake8.
6. Actualizar documentación o plan si cambia el comportamiento.
7. No commitear sin pedido explícito del usuario.

## Definition of Done

- Tests verdes sin red.
- Sin secretos ni credenciales en el diff.
- Sin cambios de semántica de riesgo no documentados.
- Sin llamadas bloqueantes nuevas dentro de async.
- Sin look-ahead ni divergencias paper/backtest/live no justificadas.
- Cambio explicado en el resultado final con archivos y validaciones.
