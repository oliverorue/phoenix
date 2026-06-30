# Arquitectura de Phoenix

## Visión general

Bot de trading multi-símbolo para Binance USDⓈ-M Futures (Testnet/Mainnet) con estrategia scalping, 
gestión de riesgo avanzada, dashboard web, backtesting y watchdog.

```
┌─────────────────────────────────────────────────────────┐
│                      bot/main.py                        │
│                    TradingBot (orquestador)              │
├─────────────────────────────────────────────────────────┤
│ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ │
│ │SymbolRunner│ │SymbolRunner│ │...     │ │ Scheduler  │ │
│ │ XRPUSDT   │ │ DOGEUSDT  │ │        │ │ (tareas    │ │
│ └─────┬─────┘ └─────┬─────┘ └──────────┘ │ periódicas)│ │
│       │              │                    └────────────┘ │
│       └──────┬───────┘                                   │
│              ▼                                           │
│  ┌─────────────────────┐   ┌────────────────────┐       │
│  │   core/             │   │   dashboard/       │       │
│  │  ├ strategy_engine  │   │  ├ app.py (Flask)  │       │
│  │  ├ risk_manager     │   │  ├ shared_state    │       │
│  │  ├ execution        │   │  └ bot_controller  │       │
│  │  ├ data_feed        │   └────────────────────┘       │
│  │  ├ ws_feed          │                                │
│  │  ├ position_manager │   ┌────────────────────┐       │
│  │  ├ trade_journal    │   │   watchdog.py      │       │
│  │  ├ backtester       │   │  (health checks    │       │
│  │  ├ indicators       │   │   + notificaciones)│       │
│  │  ├ notifier         │   └────────────────────┘       │
│  │  ├ health           │                                │
│  │  ├ database         │   ┌────────────────────┐       │
│  │  ├ api_resilience   │   │   config/          │       │
│  │  ├ market_data      │   │  ├ loader.py       │       │
│  │  └ exchange_client  │   │  ├ settings.yaml   │       │
│  └─────────────────────┘   │  └ secrets.env    │       │
│                             └────────────────────┘       │
└─────────────────────────────────────────────────────────┘
```

## Flujo principal

1. **TradingBot.run()** inicia todos los runners y el scheduler
2. Cada **SymbolRunner.run_cycle()** ejecuta un ciclo por símbolo:
   - Obtiene datos de mercado (WebSocket o REST via ExchangeClient)
   - Calcula indicadores (RSI, ADX, MAs, patrones de velas)
   - Evalúa señal de trading via ScalpingStrategy
   - Valida riesgo (drawdown, SL, RR, spread, stops diarios)
   - Si hay señal válida → ejecuta orden via OrderExecutor (o PaperExecutor)
   - Si hay posición abierta → PositionManager monitorea y decide TP/SL/trailing
3. El **Scheduler** ejecuta tareas periódicas:
   - Limpieza de CSVs antiguos (>14 días)
   - Backup de DB
   - Health checks de sistema
4. El **Dashboard** (Streamlit) expone estado en tiempo real
5. El **Watchdog** monitorea salud del bot y notifica vía Telegram/Discord

## Capa de abstracción ExchangeClient

`bot/core/exchange_client.py` implementa la interfaz `ExchangeClientProtocol` (`bot/core/protocols.py`)
y abstrae toda la comunicación con Binance USDⓈ-M Futures:

- **REST**: klines, balance, órdenes, instrument info
- **WebSocket**: klines en tiempo real, orderbook depth
- **Reintentos**: exponential backoff + circuit breaker
- **Reconexión automática**: WebSocket con backoff + jitter

## Módulos principales

### core/strategy_engine.py
- `ScalpingStrategy.evaluate()`: Evalúa señal LONG/SHORT basada en:
  - Tendencia (MAs 5/10/20, ADX)
  - Momentum (RSI)
  - Pullback a EMA20
  - Patrones de velas (pin bar, engulfing, shooting star, martillo)
  - Señal de mercado (funding rate)
  - Score ponderado (0-100)
- `is_session_active()`: Filtro por horario de sesión

### core/risk_manager.py
- `calculate_position_size()`: Tamaño según riesgo % y fase
- `validate_drawdown()`: Drawdown diario + por sesión (80% del límite)
- `validate_sl_distance()` / `validate_rr()`: SL y RR mínimos
- `register_stop()`: Control de stops diarios
- `get_phase_config()`: Config por fase según balance
- `update_balance_for_drawdown()`: Tracking de peak balance

### core/execution.py
- `open_position()`: Coloca orden market + SL + TPs
- `close_position()`: Cierre de posiciones
- `_has_real_position()`: Verifica contra exchange para evitar desync
- `_place_market_order()`: Orden market con SL inmediato
- `update_stop_loss()` / `activate_trailing_stop()`: SL dinámico

### core/data_feed.py
- `get_klines()`: Velas OHLCV vía REST (async con retry)
- `get_balance()`: Balance USDT
- `get_instrument_info()`: Filtros del símbolo (qtyStep, min/max)
- `get_spread_percent()`: Spread bid-ask
- `wait_for_next_candle()`: Sincronización con velas

### core/ws_feed.py
- WebSocket para klines 15m y 1h en tiempo real via BinanceFuturesClient
- Reconexión automática gestionada por exchange_client.py
- Cache thread-safe de DataFrames
- Orderbook en tiempo real (bid/ask) con spread vivo

### core/backtester.py
- `BacktestEngine.run()`: Simulación histórica con datos reales
- Fees, slippage, comisiones configurable
- Múltiples timeframes (15m + 1h)
- Offline mode con instrument_cache desde CSV
- Reporte con métricas: win rate, Sharpe, max DD, profit factor

### core/indicators.py
- `calc_1h_indicators()`: MAs, ADX, MA spread
- `calc_15m_indicators()`: RSI, ATR, MAs de volumen, EMA20, velas
- Patrones: pin bar, engulfing, shooting star, martillo
- `is_market_ranging()`: Detección de mercado lateral

### core/position_manager.py
- `update()`: Monitoreo continuo de posición abierta
- TP1 parcial, breakeven temprano, trailing SL
- Reversal detection (cambio de tendencia intrabarra)

### core/notifier.py
- Notificaciones vía Telegram y Discord
- Eventos: trade open/close, drawdown, errores

### core/api_resilience.py
- `CircuitBreaker`: Previene llamadas en cascada
- `RateLimiter`: Control de tasa de requests
- `api_retry` / `api_retry_async`: Decoradores con backoff exponencial

### core/market_data.py
- `MarketDataProvider`: Funding rate + Open Interest
- Señales de mercado basadas en funding rate extremo

### core/scheduler.py
- Tareas periódicas: cleanup CSVs, backup DB, health check

### core/health.py
- `HealthMonitor`: Checks de CPU, memoria, API, latencia, datos

### core/state_recovery.py
- Persistencia de estado crítico entre reinicios

## Configuración

- `config/settings.yaml`: Fases, símbolos, parámetros de riesgo
- `config/secrets.env`: API keys (no versionado)
- `config/loader.py`: Carga y validación de config

### core/exchange_recovery.py
- `ExchangeStateRecovery`: Recupera posiciones abiertas, órdenes TP y estado persistente.
- Separado de TradingBot para mejor testabilidad y mantenimiento.
- `recover_positions()`: Sincroniza flags en memoria con el exchange.
- `recover_persistent_state()`: Restaura TP1/TP2 prices desde JSON.
- `recover_tp_orders()`: Recoloca órdenes limit de TP perdidas tras reinicio.

### core/protocols.py
- Protocols PEP 544 para inyección de dependencias con typing estructural.
- `DataFeedProtocol`, `NotifierProtocol`, `JournalProtocol`, `StrategyProtocol`
- `RiskManagerProtocol`, `ExecutorProtocol`
- Permite sustituir implementaciones reales por mocks en tests.

### core/exchange_client.py
- `BinanceFuturesClient`: Cliente completo para Binance USDⓈ-M Futures.
- `subscribe_ws()`: WebSocket combinado con reconexión automática.
- `close_ws()`: Cierre graceful de conexión WebSocket.

## Tests

```
tests/
├── test_api_resilience.py    # Circuit breaker, rate limiter, retry
├── test_config_loader.py     # Carga de settings.yaml
├── test_config_validation.py # Validación con Pydantic
├── test_database.py          # SQLite CRUD
├── test_execution.py         # OrderExecutor (incl. _has_real_position)
├── test_indicators.py        # Indicadores y patrones
├── test_integration.py       # Tests de integración
├── test_latency.py           # Latency tracking
├── test_market_data.py       # Funding rate, OI, señales
├── test_notifier.py          # Telegram/Discord
├── test_position_manager.py  # PositionManager
├── test_risk_manager.py      # RiskManager (incl. drawdown)
├── test_strategy.py          # ScalpingStrategy
├── test_symbol_runner.py     # SymbolRunner (ciclo completo)
└── test_trade_journal.py     # TradeJournal CSV
```

## CI/CD

GitHub Actions (`.github/workflows/tests.yml`):
- Python 3.12, 3.13, 3.14
- Linting (flake8)
- Type checking (mypy)
- Test suite completa

## Despliegue

```bash
# Docker
docker compose up -d

# Directo
python3 -m bot.main
```
