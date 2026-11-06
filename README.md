# 🔥 Phoenix — Binance USDⓈ-M Futures Scalping Bot

> **Versión:** v5.1 — Calibrado para capital mínimo de **$50 USD**
> 
> Bot de scalping algorítmico para Binance Futures, con estrategia basada en pullbacks,
> fases de escalamiento automático, Kelly sizing dinámico y paper trading.

---

## 🎯 ¿Para quién es este bot?

| Perfil | ¿Recomendado? |
|--------|:-------------:|
| 🟢 Capital **$50-100** | ✅ **Sí** — Solo XRPUSDT, riesgo 0.6%, objetivos realistas |
| 🟢 Capital **$100-500** | ✅ Sí — Más símbolos, riesgo progresivo |
| 🟡 Capital **$500-1500** | ✅ Sí — Apalancamiento 2x desde Fase 3 |
| 🔴 Capital < $50 | ❌ No — Comisiones devoran las ganancias |

> 📖 **Guía completa para $50:** [`SETUP_50USD.md`](SETUP_50USD.md)
>
> Si tienes $50 en Binance, **empieza por ahí**. Este README es la referencia general.

---

## ✨ Características

- **Scalping en pullbacks** — Entra en retrocesos dentro de tendencias fuertes (ADX > 22)
- **Multi-timeframe** — Tendencia en 1h, entrada en 15m
- **Score multi-factor** — Score 0-100 (mínimo 80 para operar): tendencia + pullback + RSI + patrones + volumen
- **Fases de escalamiento automáticas** — El bot ajusta riesgo, RR y SL según tu balance
- **Kelly Criterion dinámico** — Sizing adaptativo basado en historial de trades
- **Breakeven offset** — SL se mueve 0.08% sobre entry para cubrir comisiones
- **Trailing stop** — Después de TP1, activa trailing para capturar más ganancia
- **Protecciones** — Máx 2 stops/día, drawdown controlado, circuit breaker de API
- **Paper Trading** — Modo simulación sin riesgo real (¡úselo siempre primero!)
- **Dashboard Streamlit** — Monitoreo en tiempo real en `http://localhost:8502`
- **Detección de reversiones** — Cierra posición si hay señales contrarias fuertes

---

## 📦 Requisitos

- **Python 3.10+**
- **Cuenta en Binance** (Testnet para pruebas, Mainnet para real)
- **$50+ USD** en Binance Futures (si vas a mainnet)
- **API Keys** de Binance con permisos de Futures

---

## ⚙️ Instalación

### Opción 1: Directa

```bash
git clone <repo-url> trading_bot
cd trading_bot
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Opción 2: Docker

```bash
docker compose up -d
```

---

## 🔧 Configuración Rápida

### 1. API Keys

Edita [`config/secrets.env`](config/secrets.env):

```bash
BINANCE_API_KEY=tu_api_key
BINANCE_API_SECRET=tu_api_secret
TESTNET=true        # true para pruebas, false para real
```

### 2. Verificar Configuración

```bash
python -c "from config.loader import validate_config; msgs = validate_config(raise_on_error=False); print('\n'.join(msgs))"
```

### 2b. Modo de ejecución y seguridad

`exchange.testnet` en `config/settings.yaml` es la fuente principal:

```yaml
exchange:
  testnet: true   # true = Testnet, false = Mainnet
```

Si también definís `TESTNET` en `secrets.env`, **debe coincidir** con
`settings.yaml`; si no, el bot se niega a arrancar.

Para operar en mainnet real (`paper_trading.enabled: false` +
`exchange.testnet: false`) se exige una confirmación explícita:

```bash
LIVE_TRADING_CONFIRMED=true
```

Sin esa variable, sin API keys válidas o sin balance real, el bot no arranca.
El dashboard **no** puede iniciar/detener el bot en Docker
(`PHOENIX_DASHBOARD_ALLOW_BOT_CONTROL=false`).

### 3. Iniciar

```bash
python -m bot.main
# Dashboard: http://localhost:8502
```

---

## 🚀 Flujo de Implementación Recomendado

Paso a paso para asegurar que no pierdes dinero:

### Semana 1-2: Paper + Testnet
```yaml
# settings.yaml
exchange:
  testnet: true
paper_trading:
  enabled: true
  initial_balance: 50
```
### ✅ Validar: win rate ≥ 50%, profit factor ≥ 1.2, sin errores de API

### Semana 3-4: Paper + Mainnet
```yaml
exchange:
  testnet: false       # Mercado real
paper_trading:
  enabled: true        # Pero sin operar real
```
### ✅ Validar: bot recibe precios reales, señales coherentes

### Semana 5+: Mainnet Real
```yaml
paper_trading:
  enabled: false       # Ahora sí, dinero real
```
### ✅ Monitorear a diario las primeras 2 semanas

---

## 📊 Estrategia (Resumen Técnico)

### Filtros de Entrada

| Filtro | Parámetro | Valor |
|--------|-----------|-------|
| Tendencia | EMA50(1h) + ADX | ADX > 22, precio > EMA50 para LONG |
| Pullback | EMA20(15m) | Precio dentro de 0.8% de EMA20 |
| RSI | RSI(15m) | LONG: 22-38, SHORT: 68-75 |
| Score mínimo | — | ≥ 80/100 |
| Patrón vela | Pin bar, engulfing | +15 puntos |
| Volumen | vs MA10 | > 1.5x = +15 puntos |
| Cooldown | 3 velas 15m | Tras cada señal emitida |

### Gestión de Riesgo por Fase

| Fase | Capital | Riesgo | TP1 | TP2 | Apalancamiento |
|------|---------|--------|-----|-----|---------------|
| 0 | $0-100 | 0.6% | 2.0RR | 3.0RR | 1x |
| 1 | $100-200 | 1.2% | 2.0RR | 3.0RR | 1x |
| 2 | $200-500 | 1.5% | 2.0RR | 3.0RR | 1x |
| 3 | $500-1500 | 1.2% | 1.8RR | 3.0RR | 2x |
| 4 | $1500+ | 1.0% | 1.5RR | 3.0RR | 3x |

### Breakeven Offset

El SL **no** se mueve exactamente a `entry_price`. Se mueve 0.08% **por encima** (LONG)
o **por debajo** (SHORT) para cubrir comisiones de entrada + salida. Esto elimina las
micro-pérdidas cuando el precio roza entry y se revierte.

---

## 🧪 Backtest

```bash
# Backtest básico XRPUSDT con $50
python scripts/backtest.py --symbol XRPUSDT --capital 50 --start 2026-04-01 --end 2026-05-01

# Con más detalle
python scripts/backtest.py --symbol XRPUSDT --capital 50 --start 2026-04-01 --end 2026-05-01 --verbose
```

Resultados se guardan en `logs/backtest_XRPUSDT_*.csv`

---

## 📁 Estructura del Proyecto

```
trading_bot/
├── bot/
│   ├── main.py                     # Entry point
│   ├── core/
│   │   ├── strategy_engine.py      # Lógica de estrategia y scoring
│   │   ├── position_manager.py     # Breakeven, trailing, reversiones
│   │   ├── risk_manager.py         # Fases, drawdown, stops diarios
│   │   ├── execution.py            # Órdenes en Binance
│   │   ├── backtester.py           # Motor de backtesting
│   │   ├── kelly_sizing.py         # Kelly Criterion dinámico
│   │   ├── indicators.py           # Cálculo de indicadores técnicos
│   │   ├── paper_executor.py       # Simulador de ejecución
│   │   └── ...
│   └── dashboard/
│       └── app.py                  # Streamlit dashboard
├── config/
│   ├── settings.yaml               # Configuración principal
│   └── loader.py                   # Cargador de configuración
├── data/                           # Datos históricos (CSV)
├── logs/                           # Logs del bot
├── scripts/
│   ├── backtest.py                 # CLI de backtesting
│   └── benchmark.py                # Benchmarks de rendimiento
├── SETUP_50USD.md                  # Guía completa para $50
└── ARCHITECTURE.md                 # Documentación técnica
```

---

## 📈 Dashboard

Monitor en tiempo real via Streamlit:

```bash
# El dashboard se inicia automáticamente con el bot
# Abre: http://localhost:8502
```

**Métricas clave:**
- Balance actual y PnL diario
- Trades activos e historial reciente
- Señales con score (solo ≥ 80 se ejecutan)
- Drawdown y límites de riesgo
- Distribución LONG/SHORT

---

## 🛡️ Protecciones

- **Circuit breaker de API:** 5 fallos → 15 min de pausa
- **Máx 2 stops por día:** Límite duro para evitar overtrading
- **Máx 2 pérdidas consecutivas:** El bot se detiene solo
- **Drawdown diario 3%:** Si se supera, detiene operaciones
- **Cooldown:** 3 velas 15m entre señales del mismo tipo
- **Filtro de volatilidad:** No opera si la volatilidad es extrema
- **Notificaciones:** Telegram/Discord opcionales

---

## 🔄 CI/CD

Ver [`.github/workflows/`](.github/workflows/) para pipelines de CI/CD.

---

## 🏗️ Arquitectura

Ver [`ARCHITECTURE.md`](ARCHITECTURE.md) para documentación detallada de la arquitectura.

La comunicación entre el Orchestrator (modo arquitecto) y el implementador (modo código)
sigue el protocolo DeepSeek V4: plan claro → implementación modular → validación.

---

## 📝 Licencia

MIT — Para uso educativo y personal. No es asesoría financiera.

> ⚠️ **ADVERTENCIA:** Trading con apalancamiento conlleva riesgo de pérdida total del capital.
> Este bot es una herramienta técnica, no una garantía de ganancias. Úselo bajo su propia responsabilidad.
