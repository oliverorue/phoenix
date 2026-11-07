# AGENTS.md — Phoenix v5.1 (monitor de señales)

> **Modo actual: monitor-only.** Este proyecto **NO ejecuta órdenes**. Es un
> radar/screener de señales para **Binance USDⓈ-M Futures** que muestra anomalías
> y tamaño sugerido para operar manualmente. No reintroducir ejecución de órdenes,
> API keys privadas ni `PaperExecutor` sin pedido explícito del usuario.

Este archivo es la referencia obligatoria para cualquier agente que trabaje en este repositorio. Antes de tocar código, leerlo y respetar sus invariantes.

## Contexto del proyecto

Phoenix es un **monitor global de mercado**:

- `bot/scanner/` — radar async que escanea cientos de pares por anomalías
  (order book imbalance, volume delta, funding extremo, liquidaciones en cascada)
  y rankea las **mejores N señales** (confianza + confluencia + liquidez + frescura).
- `bot/monitor.py` — monitor de un solo símbolo (la estrategia legacy, solo lectura).
- `bot/dashboard/` — UI Streamlit que muestra las tarjetas LONG/SHORT, el tamaño
  Kelly y permite abrir trades paper (dry-run) que se persisten en SQLite.

El objetivo es **calidad de señal y gestión de riesgo**, no ejecución automática.

## Stack real (no inventar tecnologías)

- **Python** `>=3.12`.
- **Exchange**: `python-binance` (REST/WS). **No hay `ccxt`.**
- **Async**: `asyncio`, `websockets`.
- **Datos**: `pandas`, `numpy`.
- **Config/validación**: `pydantic` v2, `PyYAML`, `python-dotenv`.
- **Logging**: `loguru`.
- **Dashboard**: `streamlit` (sin plotly; el radar renderiza HTML).
- **Persistencia**: SQLite (`data/paper_trades.db` para trades paper).

No agregar dependencias nuevas sin justificar que el stack actual no resuelve el problema.

## Reglas no negociables

### 1. Monitor-only (invariante de seguridad)

- No hay camino de ejecución: ni órdenes, ni `PaperExecutor`, ni API keys privadas.
- El scanner usa **solo datos públicos** de Binance; no requiere keys.
- `monitor_only: true` en `config/settings.yaml` → `MONITOR_ONLY` en `config/loader.py`.
- Si alguna vez se evalúa reintroducir ejecución, es una decisión explícita del usuario, con keys en `config/secrets.env` (nunca en la UI) y `reduceOnly` para cierres.

### 2. Correctitud contra Binance (WS por categoría)

Binance migró (2026) los WebSocket de USDⓈ-M futures a rutas por categoría. La URL legacy solo sirve la categoría `public`; las demás se descartan **en silencio** (handshake 101 y luego 0 frames, sin error):

- **`/public`**: `@depth`, `@bookTicker`.
- **`/market`**: `@aggTrade`, `@markPrice`, `@kline`, `@miniTicker`, `!miniTicker@arr`, `@forceOrder`, `!forceOrder@arr`.
- **`/private`**: user data (no se usa).

Reglas:
- Ruteá cada stream a su categoría (`/public` vs `/market`). No mezcles categorías en un mismo socket combinado.
- Detección de stream muerto: reconectá si una conexión no recibe datos en ~60 s.
- Los intervalos se normalizan en un solo lugar (`timeframes.py`): `"15" → "15m"`, `"60" → "1h"`.

### 3. Asincronía

- Ninguna llamada HTTP/DB síncrona dentro de una corrutina. `python-binance` es síncrono: usá `await asyncio.to_thread(...)`.
- Toda llamada de red maneja timeout, reconexión con backoff exponencial y cierre limpio de tasks (sin leaks).
- No usar `time.sleep()` en código async; usar `asyncio.sleep()`.

### 4. Memoria (8 GB + ZRAM, cero leaks)

- **Streams combinados**, no un socket por símbolo: agrupá símbolos por shard (`symbols_per_shard`).
- Estado por símbolo **acotado** (`deque(maxlen=...)`); nunca historial ilimitado ni DataFrames por evento.
- Cola de señales con límite (`signal_queue_maxlen`) y cooldown por símbolo+dirección.
- Cancelación limpia: `finally` que cancela tasks y cierra sockets.

### 5. Precisión numérica

- `Decimal` solo en bordes financieros (balances, fees, sizing). `float` en indicadores y cálculos vectorizados.
- No mezclar `Decimal` con arrays de `numpy`/`pandas`.

### 6. Testing

- Todo cambio entra con test de regresión.
- Suite por defecto sin red: `pytest -m "not integration"`.
- Antes de terminar:
  ```bash
  pytest -m "not integration"
  flake8 bot/ --select=E9,F63,F7,F82
  ```

## Mapa de arquitectura

| Área | Archivos |
|---|---|
| Radar global (scanner) | `bot/scanner/market_scanner.py`, `anomaly_engine.py`, `ranker.py`, `risk_engine.py`, `config.py`, `models.py` |
| Ejecución (paper/live) | `bot/execution/models.py`, `state_machine.py`, `manager.py`, `sizing.py`, `store.py`, `broker.py` |
| Monitor 1 símbolo | `bot/monitor.py` |
| Dashboard | `bot/dashboard/app.py`, `scanner_page.py`, `scanner_bridge.py` |
| Datos/exchange | `bot/core/data_feed.py`, `exchange_client.py`, `market_data.py`, `api_resilience.py`, `rate_limiter.py`, `timeframes.py` |
| Estrategia | `bot/core/strategy_engine.py`, `indicators.py` |
| Riesgo | `bot/core/kelly_sizing.py` |
| Config | `config/loader.py`, `config/settings.yaml`, `config/secrets.env.example` |
| Deploy | `scripts/start.sh`, `docker-entrypoint.sh`, `phoenix_ctl.sh`, `deploy/systemd/` |

## Protocolo de cambio

1. Leer los archivos afectados.
2. Identificar el invariante que el cambio puede romper.
3. Hacer el cambio más pequeño posible.
4. Agregar/actualizar tests.
5. Correr `pytest -m "not integration"` y el gate de flake8.
6. Actualizar `PLAYBOOK.md`/README si cambia el comportamiento.
7. No commitear sin pedido explícito del usuario.

## Definition of Done

- Tests verdes sin red.
- Sin secretos ni credenciales en el diff.
- Sin llamadas bloqueantes nuevas dentro de async.
- Sin reintroducir ejecución ni API keys privadas.
- Sin cambios de umbrales de riesgo/ranking no documentados.
- Cambio explicado en el resultado final con archivos y validaciones.
