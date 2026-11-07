# 📡 Phoenix — Binance USDⓈ-M Futures Signal Monitor

> **Modo actual: monitor-only.** Phoenix **no ejecuta órdenes**. Es un radar de
> mercado que escanea cientos de pares, detecta anomalías (order book imbalance,
> volume delta, funding extremo, liquidaciones en cascada) y te muestra **las
> mejores 3 señales** para operar manualmente, con el tamaño Kelly ya calculado.

---

## Qué hace

- **Escáner global async**: descubre todos los perpetuos USDT-M, filtra monedas
  con poco volumen y se conecta vía WebSockets combinados (bajo consumo de RAM).
- **Motor de anomalías**: OBI, volume delta, funding rate y cascadas de liquidación.
- **Ranking**: ordena por confianza + confluencia + liquidez + frescura y muestra
  solo las mejores N.
- **Gestión de riesgo**: tamaño Kelly por señal, capital y riesgo configurables.
- **Paper automático**: el botón "Operar" abre un trade dry-run que se gestiona
  solo (SL → TP1 parcial → breakeven → trailing → TP2) y se persiste en SQLite;
  el Kelly aprende solo del PnL de los trades.

## Instalación

```bash
cd phoenix
pip install -r requirements.txt
cp config/secrets.env.example config/secrets.env   # solo si querés alertas
```

## Uso

```bash
# Dashboard (recomendado)
streamlit run bot/dashboard/app.py

# Radar standalone (consola)
python3 -m bot.scanner.market_scanner

# Monitor de un solo símbolo
python3 -m bot.monitor --once
```

## Cómo operar

Leé [`PLAYBOOK.md`](PLAYBOOK.md): qué hacer, cómo leer una tarjeta, **cuánto
tiempo estar en un trade (scalp de 15 min a 2 h)**, SL/TP y reglas de riesgo.

## Estructura

```
bot/
  scanner/               # radar global: market_scanner, anomaly_engine, ranker, risk_engine
  execution/             # trades: models, state_machine, manager, sizing, store, broker
  monitor.py             # monitor de un símbolo (estrategia legacy, solo lectura)
  dashboard/             # app.py (Streamlit), scanner_page, scanner_bridge
  core/                  # data_feed, exchange_client, market_data, indicators,
                         # strategy_engine, kelly_sizing, notifier, ...
config/
  settings.yaml          # toda la configuración (scanner, monitor, fases, ...)
  loader.py              # lee y valida settings.yaml
scripts/                 # start.sh, docker-entrypoint, phoenix_ctl, systemd
```

## Configuración

Todo en `config/settings.yaml`, sección `scanner`:

| Parámetro | Qué hace |
|---|---|
| `min_quote_volume_24h` | Filtra monedas con poco volumen |
| `max_symbols` | Tope del universo rastreado |
| `min_confidence` / `min_reasons` | Piso de calidad de las tarjetas |
| `top_n` | Cuántas señales mostrar |
| `w_*` | Pesos del ranking (confianza, confluencia, liquidez, frescura) |
| `obi_extreme` / `volume_zscore` / `delta_extreme` / `funding_extreme` | Sensibilidad del motor |

## Tests

```bash
pytest -m "not integration"
flake8 bot/ --select=E9,F63,F7,F82
```

## ⚠️

El radar no garantiza ganancias. Valida en papel antes de arriesgar plata real.
