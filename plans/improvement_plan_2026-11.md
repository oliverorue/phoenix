# 🔧 Plan de Mejora — Phoenix v5.1

> **Fecha:** 2026-11-06
> **Estado:** En ejecución — Fase 0 completada (2026-11-06)
> **Objetivo:** llevar el bot de “código con buenas ideas” a un sistema seguro, verificable y reproducible en Testnet/Paper antes de arriesgar capital real.
> **Regla principal:** no se toca la estrategia ni se agregan features nuevas hasta que el motor de datos, riesgo, ejecución y backtest sea confiable.

---

## 1. Diagnóstico que motiva el plan

El análisis encontró fallos que impiden el funcionamiento básico:

- Las velas live usan intervalos inválidos para Binance (`"15"`/`"60"` en vez de `"15m"`/`"1h"`): `bot/core/data_feed.py:160,164` + `bot/core/exchange_client.py:779`.
- `get_instrument_info()` devuelve los filtros del primer símbolo de Binance, no del solicitado: `bot/core/exchange_client.py:896`.
- El `RiskManager` queda en modo backtest con balance congelado: `bot/core/symbol_runner.py:47,50` + `bot/core/risk_manager.py:85`.
- El drawdown, los stops diarios y el circuit breaker no reflejan la realidad: `bot/core/risk_manager.py:349-363,552-594` + `bot/core/symbol_runner.py:86-88`.
- El cooldown de la estrategia no avanza en vivo: `bot/core/strategy_engine.py:584` (solo se llama desde `backtester.py:775`).
- El paper trading no simula SL, TP2 ni TP parcial: `bot/core/position_manager.py:40-89` + `bot/core/paper_executor.py:174-234`.
- El cierre de posición puede enviar cantidad completa aunque TP1 ya ejecutó parcial: `bot/core/execution.py:313-332`.
- El trailing stop se coloca en el lado equivocado y se dispara/rechaza: `bot/core/position_manager.py:79` + `bot/core/exchange_client.py:724-758`.
- El shutdown no cierra posiciones: `bot/core/shutdown.py:222-236`.
- El backtest pagina una vela por request y congela la ventana 1h: `bot/core/backtester.py:334-337,769-770`.
- El CI actual no puede pasar: faltan dependencias, lint/types rojos, tests fallando y un test de integración que se cuelga.
- El build editable está roto: `pyproject.toml:3`.

**Conclusión:** el orden correcto es **primero hacer que el motor funcione y sea medible**, después discutir rentabilidad.

---

## 2. Objetivos y criterios de éxito

### 2.1. Objetivo general

Tener un bot que, en Paper/Testnet:

1. Obtenga datos correctos de Binance.
2. Dimensione y proteja posiciones con el balance real.
3. Ejecute entradas, TP1, TP2 y SL sin desincronizarse.
4. Sobreviva reinicios y shutdown sin dejar posiciones huérfanas.
5. Produzca backtests reproducibles y comparables con paper.
6. Pase CI, lint, tipos y tests sin red.

### 2.2. Criterios de aceptación globales (Definition of Done)

- [ ] `pytest` verde en menos de 2 minutos, sin acceso a red.
- [ ] `flake8` y `mypy` verdes (sin `--exit-zero` en el gate principal).
- [ ] `pip install -e .` funciona en un entorno limpio.
- [ ] `docker compose up -d` funciona en un clon limpio.
- [ ] Smoke test Testnet: 30 velas 15m/1h, instrumento XRP con `stepSize=0.1`, orden market + SL + TP aceptada.
- [ ] Test de ciclo completo en Testnet: abrir, TP1 parcial, mover SL, cerrar, verificar estado y DB.
- [ ] Test de reinicio: posición abierta → reinicio → posición, SL y TPs recuperados.
- [ ] Test de shutdown: posición abierta → SIGTERM → posición cerrada o estado persistido correctamente.
- [ ] Paper trading de 2 semanas con SL/TP reales, comisiones, drawdown y stops coherentes.
- [ ] Backtest con comisiones, parciales y sin look-ahead, reproducible con la misma data.
- [ ] Resultados paper vs backtest comparables dentro de un rango razonable.
- [ ] Dashboard sin posibilidad de lanzar un segundo bot y sin control anónimo.

### 2.3. No objetivos de este plan

- Aumentar rentabilidad.
- Agregar más símbolos.
- Subir apalancamiento.
- Agregar modelos de IA/ML.
- Optimizar parámetros de estrategia.

Esos puntos se retoman **después** de los gates de seguridad.

---

## 3. Principios de implementación

1. **Safety-first:** ante datos dudosos, spread desconocido, balance no disponible o estado inconsistente, el bot no opera.
2. **Fail-fast:** en modo real, si faltan keys, balance o instrumento, el arranque falla; no hay fallback a $100.
3. **Una sola fuente de verdad:** configuración tipada, estado de posición y balance.
4. **Live, paper y backtest comparten la misma semántica:** mismos SL/TP, mismos parciales, mismas comisiones.
5. **Cada fix entra con test de regresión.**
6. **Nada de live hasta pasar los gates G0–G4.**

---

## 4. Fases de trabajo

## Fase 0 — Congelamiento y guardrails (0.5–1 día)

**Objetivo:** evitar que alguien opere por accidente mientras se corrige el motor.

### Tareas

- [ ] Agregar un flag explícito de seguridad: `LIVE_TRADING_CONFIRMED=true` obligatorio para `paper_trading.enabled: false` en mainnet.
- [ ] Eliminar el fallback a `$100` en modo real (`bot/main.py:171-178`). Si no hay balance, abortar.
- [ ] Validar al arranque: keys presentes, `TESTNET` coherente con `exchange.testnet`, balance > 0, instrumento cargado.
- [ ] Unificar `exchange.testnet` de `settings.yaml` con la variable `TESTNET` (`config/loader.py:29`). Una sola fuente.
- [ ] Deshabilitar por defecto el exporter `console` de tracing en producción; usar OTLP o `enabled: false`.
- [ ] Bloquear el botón “Iniciar” del dashboard cuando ya hay un bot externo o cuando el entorno es producción.
- [ ] Agregar `pytest` marker `integration` y excluirlo por defecto.
- [ ] Agregar timeout global a pytest (`pytest-timeout`) para que un test colgado no bloquee CI.

### Aceptación

- [ ] Con `paper_trading.enabled: false` y sin confirmación explícita, el bot no arranca.
- [ ] Sin keys en modo real, el bot falla en el arranque con mensaje claro.
- [ ] `pytest` por defecto no toca red.

---

## Fase 1 — Capa de datos y exchange (2–3 días)

**Objetivo:** que `current_15m()`, `current_1h()` y `get_instrument_info()` devuelvan datos correctos del símbolo correcto.

### Tareas

- [ ] Crear una normalización única de intervalos:
  - `"15" → "15m"`
  - `"60" → "1h"`
  - `"1h" → "1h"`
  - Aplicarla en `DataFeed`, `BinanceFuturesClient` y `WebSocketFeed`.
- [ ] Corregir `get_instrument_info()`:
  - Buscar `raw_sym` por `symbol == sym` (`exchange_client.py:896`).
  - Leer filtro `NOTIONAL` además de `MIN_NOTIONAL` (`exchange_client.py:900`).
  - Devolver error si el símbolo no existe.
- [ ] Centralizar el mapeo a `qtyStep`, `minOrderQty`, `maxOrderQty`, `minNotionalValue` en un solo lugar.
- [ ] Corregir `_format_quantity()` para usar la precisión del símbolo solicitado.
- [ ] Pasar las llamadas REST síncronas a `asyncio.to_thread()` o a un cliente async real (`httpx`/`aiohttp`), para no bloquear el event loop.
- [ ] Decidir sobre WebSocket:
  - **Opción A (recomendada):** integrarlo de verdad en `SymbolRunner` con fallback REST y seed inicial.
  - **Opción B:** deshabilitarlo por configuración hasta que esté corregido.
- [ ] Si se mantiene WebSocket:
  - Corregir URL a `/stream?streams=...` (`exchange_client.py:985-987`).
  - Corregir `kline_60m` → `kline_1h` (`ws_feed.py:124-131`).
  - No marcar `connected=True` hasta recibir el primer mensaje válido.
  - Implementar fallback REST real en `current_15m()` / `current_1h()`.
- [ ] Corregir spread:
  - Si no se puede obtener, devolver un valor conservador que **bloquee** la entrada.
  - No usar `0.0` como “sin datos”.

### Tests obligatorios

- [ ] Test con cliente mockeado: `current_15m()` devuelve 30 velas y usa `"15m"`.
- [ ] Test con cliente mockeado: `current_1h()` devuelve 30 velas y usa `"1h"`.
- [ ] Test de `get_instrument_info("XRPUSDT")` con dos símbolos en el payload: debe devolver el step de XRP, no el de BTC.
- [ ] Test de filtro `NOTIONAL`.
- [ ] Test de spread desconocido → bloquea.
- [ ] Smoke test opcional contra Testnet (marcado `integration`).

### Aceptación

- [ ] Un script `scripts/smoke_data.py` confirma velas y filtros reales de Testnet.
- [ ] `run_cycle()` ya no cae en “error en ciclo” por datos.

---

## Fase 2 — Motor de riesgo live (3–4 días)

**Objetivo:** que las decisiones de riesgo usen el balance real y que las protecciones realmente se activen.

### Tareas

- [ ] Separar `set_backtest_balance()` de `set_initial_allocation()`.
  - `_backtest_balance` solo se usa en backtest.
  - En live/paper, el balance se actualiza cada ciclo.
- [ ] Propagar balance:
  - Live: balance de `DataFeed.get_balance()`.
  - Paper: `PaperExecutor.paper_balance`.
  - `runner.balance` y `TradingBot.balance` deben actualizarse.
- [ ] No borrar el notifier en `set_balance()` (`risk_manager.py:95`).
- [ ] Reset diario correcto:
  - `capital_inicio_dia = balance_actual` al cambiar de día (`risk_manager.py:361-363`).
  - Reset de stops y pérdidas consecutivas.
- [ ] Corregir conteo de stops:
  - Registrar stop **solo** cuando el cierre fue por SL o pérdida.
  - Resetear `_consecutive_losses` cuando el trade gana.
  - No contar cierres por TP, manuales o por reversión como “stop”.
- [ ] Aplicar `dias_excluir` en `is_trading_session()` (`risk_manager.py:630-645`).
- [ ] Cooldown live:
  - Llamar `tick_cooldown()` una vez por vela 15m cerrada.
  - Llamar `register_trade_outcome()` al cerrar cada trade.
  - Resetear cooldown al cambiar de día/fase.
- [ ] Corregir `validate_rr`:
  - Decidir explícitamente si valida TP1 o TP2.
  - Usar la misma semántica en live, paper y backtest.
- [ ] Usar `spread_max` por símbolo desde el portfolio.
- [ ] Usar `leverage` y `risk_per_trade` por símbolo o eliminarlos de la configuración para no engañar.
- [ ] `PortfolioRisk.can_open(notional_to_add)`:
  - Estimar el nocional de la nueva posición antes de abrir.
  - Pasarlo desde `SymbolRunner` (`symbol_runner.py:176`).
- [ ] Corregir VaR:
  - No usar `abs(pnl_pct)` para calcular volatilidad.
  - Usar retornos con signo o una métrica explícita de pérdida.

### Tests obligatorios

- [ ] Live: balance cambia → fase y sizing cambian.
- [ ] Drawdown diario se reinicia al cambiar de día.
- [ ] Stop por SL incrementa stops; TP no.
- [ ] Pérdida consecutiva se resetea con una ganancia.
- [ ] Martes excluido en fase 0.
- [ ] Cooldown avanza con velas y se libera.
- [ ] Exposición total bloquea la segunda entrada cuando corresponde.

### Aceptación

- [ ] Test de simulación de 10 trades con PnL conocido: stops, drawdown y circuit breaker coinciden con lo esperado.
- [ ] El dashboard muestra el balance real/paper actualizado.

---

## Fase 3 — Ejecución y gestión de posición (4–5 días)

**Objetivo:** que el estado interno coincida con el exchange en todo momento.

### Tareas

- [ ] Usar el precio real de fill:
  - Leer `avgPrice` de la respuesta de la orden market.
  - Guardar entry real, qty real y fees reales.
- [ ] Reconciliar TP1/TP2:
  - Detectar fills de órdenes reduce-only.
  - Reducir `executor.quantity` después de TP1.
  - Marcar `tp1_hit` solo cuando el fill ocurrió, no solo cuando el precio lo tocó.
- [ ] Cierre seguro:
  - Enviar cierre con `reduceOnly=true`.
  - Usar la cantidad restante, no la original.
  - Cancelar solo las órdenes del bot, no todas las del símbolo.
- [ ] Trailing stop:
  - Implementar trailing client-side real: actualizar `update_stop_loss()` periódicamente.
  - No colocar el stop inicial en el lado equivocado.
  - En paper, simular el movimiento del SL.
- [ ] Reversal:
  - Revisar la regla `precio < MA20(1h)`; agregar confirmación o umbral.
  - Simular la misma regla en backtest.
- [ ] Shutdown:
  - Buscar `executor.position_open`, no `runner.position` (`shutdown.py:222-225`).
  - Hacer `close_position()` async y esperar.
  - Cancelar órdenes abiertas.
  - Guardar estado final.
- [ ] Recuperación tras reinicio:
  - No cancelar el SL.
  - Recolocar SL y TPs faltantes.
  - No sobrescribir datos del exchange con JSON viejo sin validar.
- [ ] Idempotencia:
  - Agregar `newClientOrderId` único por intento.
  - Evitar órdenes duplicadas tras timeout/reintento.

### Tests obligatorios

- [ ] Market fill con slippage: entry real ≠ signal entry, y el estado usa el fill.
- [ ] TP1 parcial: qty se reduce al 35%.
- [ ] Cierre posterior a TP1: envía 35% con reduceOnly.
- [ ] SL y TP se recolocan tras reinicio.
- [ ] Shutdown con posición abierta cierra y cancela órdenes.
- [ ] Trailing actualiza el SL en la dirección correcta.
- [ ] Timeout de orden no duplica posición.

### Aceptación

- [ ] Test de ciclo completo en Testnet: abrir → TP1 → breakeven → cierre → estado consistente.
- [ ] No se puede abrir una posición opuesta por error.

---

## Fase 4 — Paper trading con paridad real (3–4 días)

**Objetivo:** que paper sea un simulador confiable, no un “modo sin SL”.

### Tareas

- [ ] `PaperExecutor` debe:
  - Evaluar SL y TP en cada actualización.
  - Ejecutar TP1 parcial según `tp1_porcentaje`.
  - Ejecutar TP2 sobre el remanente.
  - Aplicar slippage y comisión en entrada y salida.
  - Reducir cantidad después de TP1.
  - Soportar trailing stop moviendo el SL.
- [ ] `PositionManager` no debe depender del exchange para paper.
- [ ] Sincronizar `paper_balance` con `runner.balance` y el dashboard.
- [ ] Mantener un historial de equity por ciclo.
- [ ] Reconciliación en paper: no llamar a `_ensure_client()` ni al exchange real.
- [ ] Comparar paper vs backtest con el mismo dataset y mismas reglas.

### Tests obligatorios

- [ ] Trade perdedor toca SL → cierra en SL con slippage.
- [ ] Trade ganador toca TP1 → cierra 65%, mueve SL, luego TP2 cierra 35%.
- [ ] Trailing stop se activa y mueve el SL.
- [ ] Comisiones se descuentan del balance y del PnL reportado.
- [ ] No se puede abrir una segunda posición simultánea en el mismo símbolo.

### Aceptación

- [ ] Paper trading de 2 semanas sin posiciones “eternas”.
- [ ] Curva de equity coherente.
- [ ] Resultados comparables con backtest.

---

## Fase 5 — Backtester y optimizador (4–6 días)

**Objetivo:** que los backtests sean reproducibles y no tengan sesgos.

### Tareas

- [ ] Corregir paginación histórica:
  - Usar `candles[0][0] - 1` (`backtester.py:334-337`).
  - Agregar límite de iteraciones y log de progreso.
- [ ] Corregir ventana 1h:
  - Usar las últimas `WARMUP` velas disponibles, no las primeras.
  - `df_1h.iloc[max(0, n_1h - WARMUP):n_1h]` (`backtester.py:769-770`).
- [ ] No descartar trades con `duration_candles == 0` (`backtester.py:825`).
- [ ] Simular TP parcial:
  - 65% en TP1, 35% en TP2.
  - Breakeven con offset sobre el remanente.
- [ ] Pasar `score` a `calculate_position_size()` (`backtester.py:814`).
- [ ] Unificar fees/slippage con live/paper.
- [ ] Revisar prioridad TP1 vs SL en la misma vela; documentar el criterio conservador.
- [ ] Cache de datos:
  - Validar que el CSV cubra el rango pedido.
  - No reutilizar cache parcial.
- [ ] Optimizador:
  - Crear una instancia nueva de estrategia/risk por combinación o resetear estado.
  - Walk-forward con datos OOS reales.
- [ ] Agregar tests con datasets sintéticos conocidos.

### Tests obligatorios

- [ ] Paginación: un rango de 1000 velas se descarga en ~5 requests, no en 1000.
- [ ] Ventana 1h: la última vela 1h usada corresponde al timestamp de la vela 15m.
- [ ] Trade que toca SL en la vela de entrada se cuenta como pérdida.
- [ ] TP1 parcial y TP2 producen el PnL esperado.
- [ ] Mismo dataset + mismos parámetros = mismo resultado (determinismo).

### Aceptación

- [ ] Backtest de 4 meses termina en minutos, no horas.
- [ ] Resultados reproducibles.
- [ ] El optimizador no arrastra estado entre combinaciones.

---

## Fase 6 — Persistencia y contabilidad (2–3 días)

**Objetivo:** que trades, PnL, auditoría y recuperación sean confiables.

### Tareas

- [ ] `TradeJournal` por posición/símbolo:
  - No usar un único `_open_trade` global (`trade_journal.py:20,43`).
  - Clave por `symbol` + `trade_id`.
- [ ] `trade_id` único:
  - UUID o `symbol + timestamp + secuencia`.
  - Eliminar colisiones por segundo (`trade_journal.py:44`).
- [ ] PnL neto:
  - Restar comisiones de entrada y salida.
  - Reportar gross y net por separado.
- [ ] Auditoría:
  - Llamar `audit.log_trade()` después de completar exit/PnL (`trade_journal.py:173-174`).
  - Incluir comisiones y versión real.
- [ ] DB:
  - Revisar comparación de `entry_time` ISO con `datetime('now')` (`database.py:191-200`).
  - Agregar índices por `symbol` y `entry_time`.
  - Usar WAL y timeout para concurrencia con el dashboard.
- [ ] Estado persistente:
  - Escritura atómica (tmp + replace).
  - Validar JSON viejo contra exchange antes de usarlo.
- [ ] Kelly:
  - Registrar PnL neto.
  - Respetar `enabled`, `max_streak_boost`, `conservative_*` de settings.

### Tests obligatorios

- [ ] Dos símbolos abren y cierran sin pisarse.
- [ ] Dos trades en el mismo segundo tienen IDs distintos.
- [ ] PnL de DB = PnL neto del executor.
- [ ] Auditoría verifica datos reales, no ceros.
- [ ] Reinicio con JSON viejo no resucita posición inexistente.

### Aceptación

- [ ] DB y CSV coinciden.
- [ ] El dashboard muestra el mismo PnL que el journal.

---

## Fase 7 — Infraestructura, CI y despliegue (2–3 días)

**Objetivo:** que cualquier persona pueda instalar, testear y desplegar sin magia.

### Tareas

- [ ] `pyproject.toml`:
  - `build-backend = "setuptools.build_meta"` (`pyproject.toml:3`).
  - Mover dependencias de test a `[project.optional-dependencies].dev`.
  - Incluir `streamlit`/`plotly` en `dashboard` o `all`.
- [ ] `requirements.txt`:
  - Agregar OpenTelemetry o hacerlo opcional.
  - Separar runtime y dev (`requirements-dev.txt`).
  - Fijar versiones o agregar lockfile (`uv.lock`/`poetry.lock`/`pip-tools`).
- [ ] Docker:
  - Copiar `tests/` o eliminar el comando `test` (`Dockerfile:37-40`).
  - Corregir healthcheck para validar proceso/datos, no un JSON.
  - Definir `image:` en el servicio bot o construir el dashboard desde el mismo build.
  - Alinear puerto 8501/8502.
- [ ] Makefile:
  - Quitar dependencia de `docker-compose.override.yml` o crearlo.
  - Corregir `make test` para que use tests presentes en la imagen.
- [ ] CI:
  - Instalar dependencias completas.
  - Gate real de flake8/mypy/tests.
  - `pytest -m "not integration"` por defecto.
  - Agregar timeout y cobertura mínima.
  - Eliminar el paso “Benchmark” duplicado.
- [ ] Config:
  - Unificar `settings.yaml` + env + `loader.py`.
  - Eliminar `BYBIT_*` de `config_watcher.py:250-253` y `secrets_manager.py:68-71`.
  - Integrar `SecretsManager` o eliminarlo/documentarlo como no soportado.

### Aceptación

- [ ] `pip install -e ".[dev]"` funciona.
- [ ] `docker compose up -d` funciona en limpio.
- [ ] CI verde en los 3 Python del matrix.
- [ ] `make test` ejecuta tests reales.

---

## Fase 8 — Dashboard, seguridad y observabilidad (2–3 días)

**Objetivo:** monitorear sin riesgo de duplicar bots ni exponer control.

### Tareas

- [ ] Autenticación mínima o bind a `127.0.0.1` por defecto.
- [ ] No permitir iniciar/detener/reiniciar bots desde el dashboard en producción.
- [ ] Un solo proceso bot por cuenta API:
  - Lockfile o chequeo de instancia.
  - El dashboard solo monitorea; no crea `TradingBot`.
- [ ] Estado compartido real:
  - Archivo en `data/` compartido o endpoint del bot.
  - El dashboard debe leer datos frescos, no un singleton en memoria.
- [ ] No reescribir `settings.yaml` desde el dashboard en Docker read-only.
- [ ] Métricas:
  - Corregir `session_active` (await real).
  - Corregir `drawdown_pct`.
  - Incrementar contadores de trades/wins/losses.
- [ ] Logs/tracing:
  - Console exporter deshabilitado por defecto.
  - Rotación y retención razonables.

### Aceptación

- [ ] No se puede lanzar un segundo bot desde el dashboard.
- [ ] El dashboard muestra balance, equity, drawdown y posiciones reales.
- [ ] Sin credenciales no se puede controlar el bot.

---

## Fase 9 — Documentación y limpieza (1–2 días)

### Tareas

- [ ] Actualizar README:
  - Score mínimo real (35, no 80).
  - ADX real (14 absoluto + config de fase).
  - Puerto real del dashboard.
  - No decir que el dashboard inicia solo.
  - Corregir `pip install -e .`.
- [ ] Actualizar ARCHITECTURE:
  - Streamlit, no Flask.
  - Módulos actuales, no `multi_bot.py`.
- [ ] Unificar versión en `pyproject`, README, tracing, notifier, audit.
- [ ] Eliminar referencias a Bybit.
- [ ] Documentar variables de entorno y precedencia.
- [ ] Documentar modos: backtest / paper / testnet / mainnet.
- [ ] Documentar el proceso de recuperación ante crash.

---

## 5. Plan de tests

### 5.1. Unitarios

Sin red. Mockear Binance y DB.

- Intervalos y filtros.
- Sizing, drawdown, stops, cooldown.
- TP1/TP2/SL/trailing.
- Journal/DB/auditoría.
- Backtester con datasets sintéticos.

### 5.2. Contrato (fixtures grabadas)

Guardar respuestas reales de Binance en `tests/fixtures/` y testear:

- `exchangeInfo` con varios símbolos.
- `klines` 15m/1h.
- `futures_account`.
- `futures_position_information`.
- `futures_get_open_orders`.

### 5.3. Integración Testnet (opt-in)

Marcar `@pytest.mark.integration` y correr solo manualmente o en un job separado:

- Conectar.
- Leer velas.
- Leer instrumento.
- Abrir y cerrar una posición mínima.
- Verificar SL/TP.
- Reiniciar y recuperar.

### 5.4. Paper sostenido

- 2 semanas mínimo.
- Registrar equity diaria.
- Comparar contra backtest del mismo período.
- Criterios:
  - Sin posiciones huérfanas.
  - Sin desincronización.
  - Drawdown y stops coherentes.
  - PnL paper vs backtest dentro de un rango razonable.

---

## 6. Gates de despliegue

| Gate | Requisito | Bloquea |
|---|---|---|
| G0 | Build/install/tests/lint/types verdes | Todo |
| G1 | Datos live correctos (velas + instrumento) | Paper/Testnet |
| G2 | Ciclo Testnet completo con SL/TP/parciales | Paper prolongado |
| G3 | Recuperación y shutdown seguros | Mainnet |
| G4 | 2 semanas Paper estables | Mainnet |
| G5 | Backtest reproducible y comparable | Decisiones de estrategia |

**No se pasa a G3/G4/G5 sin cerrar G0–G2.**

---

## 7. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Cambios en riesgo/ejecución introducen nuevos bugs | Tests de regresión por cada fix + simulación con PnL conocido |
| El backtest sigue dando resultados irreales | Fixtures, tests sintéticos, comparación paper vs backtest |
| Se opera en real por accidente | Flag explícito + fail-fast + dashboard bloqueado |
| Se duplica el bot | Lockfile/instancia única, dashboard solo lectura |
| Se rompe la compatibilidad de configuración | Migración documentada + validación Pydantic |
| Faltan datos históricos | Cache validado + descarga robusta + límites de iteración |
| El CI sigue rojo | Arreglar dependencias primero, luego lint/types/tests |

---

## 8. Estimación

| Fase | Esfuerzo |
|---|---|
| 0 — Guardrails | 0.5–1 día |
| 1 — Datos/exchange | 2–3 días |
| 2 — Riesgo live | 3–4 días |
| 3 — Ejecución/posición | 4–5 días |
| 4 — Paper parity | 3–4 días |
| 5 — Backtester | 4–6 días |
| 6 — Persistencia | 2–3 días |
| 7 — Infra/CI | 2–3 días |
| 8 — Dashboard/seguridad | 2–3 días |
| 9 — Docs/limpieza | 1–2 días |
| **Total** | **~24–34 días de trabajo efectivo** |

Se puede paralelizar Fase 5 con Fase 4 y Fase 7 con Fase 8 si hay más de una persona.

---

## 9. Backlog inmediato (primer sprint, 5 días)

### Día 1

- [ ] Fase 0 completa.
- [ ] Corregir `pyproject.toml` y `requirements.txt`.
- [ ] Hacer que `pytest` no toque red.
- [ ] Arreglar `test_integration` para que no cuelgue.

### Día 2

- [ ] Normalización de intervalos.
- [ ] Fix `get_instrument_info()`.
- [ ] Tests de datos con fixtures.

### Día 3

- [ ] Separar balance live/backtest.
- [ ] Propagar balance real/paper.
- [ ] Reset diario y notifier.

### Día 4

- [ ] Stops, pérdidas consecutivas y cooldown.
- [ ] `dias_excluir` live.
- [ ] Tests de riesgo.

### Día 5

- [ ] Smoke test Testnet de datos e instrumento.
- [ ] Revisión de resultados.
- [ ] Decidir si se continúa con ejecución o se ajusta el alcance.

---

## 10. Checklist de comandos

```bash
# Instalación limpia
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Tests sin red
pytest -m "not integration"

# Calidad
flake8 bot/ tests/ config/ scripts/
mypy bot/
black --check bot/ tests/ config/ scripts/
ruff check bot/ tests/ config/ scripts/

# Backtest reproducible
python scripts/backtest.py --symbol XRPUSDT --start 2026-04-01 --end 2026-05-01 --capital 50

# Smoke Testnet
python scripts/smoke_data.py --testnet

# Paper
python -m bot.main

# Docker
docker compose up -d --build
docker compose ps
docker compose logs -f bot
```

---

## 11. Decisión final

**El próximo trabajo no es “mejorar la estrategia”.** Es reparar el motor y demostrar que mide la realidad.

Orden estricto:

1. Datos correctos.
2. Riesgo correcto.
3. Ejecución correcta.
4. Paper realista.
5. Backtest confiable.
6. Recién entonces: estrategia, parámetros y rentabilidad.

Si no se respeta ese orden, cualquier resultado —paper, backtest u optimización— va a ser ficción.

---

## 12. Registro de avance

### Fase 0 — Completada (2026-11-06)

- [x] `LIVE_TRADING_CONFIRMED=true` obligatorio para mainnet real.
- [x] Eliminado el fallback a `$100` en modo real; ahora aborta sin balance.
- [x] Validación de arranque: API keys, balance > 0 e instrumento cargable.
- [x] `exchange.testnet` es la fuente principal; conflicto con `TESTNET` aborta el arranque.
- [x] El modo testnet/mainnet no se cambia en caliente (solo reiniciando).
- [x] Tracing console deshabilitado por defecto (`tracing.enabled: false`).
- [x] Dashboard bloqueado para iniciar/detener el bot en Docker (`PHOENIX_DASHBOARD_ALLOW_BOT_CONTROL=false`).
- [x] Marker `integration` + `-m 'not integration'` por defecto.
- [x] `pytest-timeout` con timeout global de 120 s.
- [x] Gate de flake8 del CI (`E9,F63,F7,F82`) en verde.
- [x] Suite por defecto en verde (sin integración de red).
- [x] Corregidos tests desactualizados y el aislamiento de `test_config_watcher`.

**Pendiente para G0 completo (Fase 7):**

- [ ] `pyproject.toml` con backend de build válido.
- [ ] OpenTelemetry en `requirements.txt` o tracing opcional.
- [ ] `mypy` en verde.
- [ ] `pip install -e .` verificado en entorno limpio.

### Fase 1 — Completada (2026-11-06)

- [x] Normalización centralizada de intervalos (`bot/core/timeframes.py`).
- [x] `get_instrument_info()` busca el símbolo solicitado (no `symbols[0]`).
- [x] Lee filtro `NOTIONAL` y `MIN_NOTIONAL` legacy.
- [x] Devuelve `min_notional`, `max_notional` y precisión correctos.
- [x] `_format_quantity()` usa el `stepSize`/precisión del símbolo solicitado.
- [x] Llamadas REST de `python-binance` envueltas con `asyncio.to_thread`.
- [x] WebSocket corregido: URL combinada `/stream?streams=`, intervalos `1h`, `connected` solo tras el primer mensaje.
- [x] WebSocket deshabilitado por defecto hasta integrar sus buffers en `SymbolRunner`.
- [x] Spread desconocido devuelve 100% (bloquea entradas), no 0%.
- [x] Tracing deshabilitado por defecto a nivel global.
- [x] Tests nuevos de timeframes, instrumento, intervalos, thread-offload y URL WS.
- [x] Smoke test Testnet: 30 velas 15m/1h, XRP `stepSize=0.1`, `minNotional=5`, spread real.

### Fase 2 — Completada (2026-11-07)

- [x] Separación explícita entre balance live/paper y balance de backtest.
- [x] `set_backtest_balance()` / `update_backtest_balance()` solo para backtest.
- [x] `set_initial_balance()` y `update_live_balance()` para live/paper.
- [x] `SymbolRunner` actualiza el balance desde `OrderExecutor`/`PaperExecutor` en cada ciclo.
- [x] El balance agregado del `TradingBot` se recalcula desde los runners.
- [x] `set_balance()` ya no borra el notificador.
- [x] `capital_inicio_dia` se reinicia correctamente al cambiar de día.
- [x] `stops_dia` cuenta solo cierres por SL; TP, manuales y reversiones no cuentan.
- [x] Las pérdidas consecutivas se resetean con una ganancia.
- [x] `register_trade_result()` reemplaza el `register_stop()` indiscriminado.
- [x] Cooldown avanza por vela cerrada, no por tick de 5 s.
- [x] `dias_excluir` se aplica en vivo.
- [x] `validate_rr` valida TP1, no TP2.
- [x] Leverage y `risk_per_trade` por símbolo se aplican al sizing.
- [x] `PortfolioRisk.can_open()` recibe el nocional estimado de la nueva posición.
- [x] VaR usa retornos con signo, no `abs()`.
- [x] Tests nuevos de balance, drawdown, stops, sesión, RR, cooldown y VaR.

### Fase 3 — Completada (2026-11-07)

- [x] `open_position` usa `avgPrice` y `executedQty` reales del fill.
- [x] `newClientOrderId` único en entrada, SL, TPs y cierres.
- [x] Reintentos de market order verifican posición antes de duplicar.
- [x] `sync_position_from_exchange()` detecta TP1 parcial y cierres SL/TP.
- [x] `quantity` se reduce al remanente real tras TP1.
- [x] El cierre usa la cantidad remanente y `reduceOnly=True`.
- [x] `_cancel_limit_orders()` cancela solo órdenes creadas por este bot.
- [x] Trailing stop real: mueve el SL detrás del extremo, sin precio de activación invertido.
- [x] TP1 se marca solo con fill confirmado, no por tocar precio.
- [x] Shutdown detecta `executor.position_open` y espera `close_position_async()`.
- [x] Recuperación ya no cancela el SL; repone SL/TPs faltantes.
- [x] Recuperación no resucita posiciones desde JSON viejo.
- [x] Si hay posición sin SL conocido, se cierra por seguridad.
- [x] Tests de fills, TP parcial, cierre remanente, trailing, shutdown y recuperación.

### Fase 4 — Completada (2026-11-07)

- [x] `PaperExecutor.update_position()` simula SL, TP1 y TP2 en cada ciclo.
- [x] TP1 cierra el porcentaje configurado y reduce la cantidad remanente.
- [x] TP2 cierra el remanente completo.
- [x] SL tiene prioridad conservadora sobre TP en el mismo tick.
- [x] Slippage en market/SL y fills limit en TPs.
- [x] Fees separados: taker para market/SL, maker para TPs.
- [x] Apalancamiento influye en el margen requerido.
- [x] Trailing stop mueve el SL y puede ejecutar el cierre en paper.
- [x] Equity mark-to-market e historial por ciclo.
- [x] `TradeJournal` suma PnL parcial y descuenta comisiones totales.
- [x] La reconciliación de exchange se salta en paper.
- [x] Tests de TP parcial, TP2, SL con slippage, trailing, equity y journal parcial.

**Siguiente:** Fase 5 — corregir el backtester (paginación, ventana 1h, vela de entrada, TP parcial, score).
