# 🏆 Phoenix Bot — Guía Completa para $50 USD en Binance

> **Modo actual: monitor-only.** Phoenix ya no ejecuta órdenes: muestra señales
> para operar manualmente. El comando operativo es `python3 -m bot.monitor`.
> Las secciones de ejecución paper/live quedan como referencia histórica.
>
> **Objetivo:** Evaluar señales de scalping con **$50 USD** en Binance USDⓈ-M Futures.
> **Lema:** Calidad > Cantidad. Preferimos 1-2 trades perfectos al día que 10 mediocres.

---

## 📋 Tabla de Contenidos

1. [Arquitectura de la Solución](#-arquitectura-de-la-solución)
2. [Configuración Inicial (Binance Testnet)](#-configuración-inicial-binance-testnet)
3. [Configuración Mainnet](#-configuración-mainnet)
4. [Estrategia de Trading para $50](#-estrategia-de-trading-para-50)
5. [Guía de Operación Diaria](#-guía-de-operación-diaria)
6. [Money Management](#-money-management)
7. [Qué Esperar (Rentabilidad Realista)](#-qué-esperar-rentabilidad-realista)
8. [Solución de Problemas](#-solución-de-problemas)
9. [Métricas de Éxito](#-métricas-de-éxito)

---

## 🏗️ Arquitectura de la Solución

### Cambios implementados para $50

| Cambio | Archivo | ¿Por qué? |
|--------|---------|-----------|
| ✅ Solo XRPUSDT activo | [`settings.yaml`](config/settings.yaml) | Único símbolo viable con $50 por liquidez y minNotional |
| ✅ Risk 0.6% por trade | [`settings.yaml`](config/settings.yaml) | Protege el capital en fase bootstrap |
| ✅ TP1-RR 2.0 / TP2-RR 3.0 | [`settings.yaml`](config/settings.yaml) | Necesitas al menos 1:2 para ser rentable tras comisiones |
| ✅ MIN_SCORE = 70 | [`strategy_engine.py`](bot/core/strategy_engine.py) | Balance calidad/cantidad para mercados con tendencia |
| ✅ ADX mínimo = 20 | [`strategy_engine.py`](bot/core/strategy_engine.py) | Tendencia mínima requerida (reducido de 22) |
| ✅ HH/LL como bonus (+10pts) | [`strategy_engine.py`](bot/core/strategy_engine.py) | Ya no es veto, suma puntos al score |
| ✅ Momentum relajado | [`strategy_engine.py`](bot/core/strategy_engine.py) | Solo veta si la vela cierra fuertemente contraria (>0.3%) |
| ✅ RSI_MIN_SCORE = 10 | [`strategy_engine.py`](bot/core/strategy_engine.py) | RSI debe contribuir significativamente |
| ✅ RR validación usa tp1_rr | [`risk_manager.py`](bot/core/risk_manager.py) | Más realista: TP1 cierra 65%, luego breakeven |
| ✅ Breakeven offset 0.08% | [`position_manager.py`](bot/core/position_manager.py) | Cubre comisiones en el SL (evita micro-pérdidas) |
| ✅ Breakeven offset en backtest | [`backtester.py`](bot/core/backtester.py) | El backtest refleja el comportamiento real |
| ✅ Máx 2 stops/día | [`settings.yaml`](config/settings.yaml) | Límite duro para evitar overtrading |
| ✅ Máx 2 pérdidas consecutivas | [`settings.yaml`](config/settings.yaml) | Para automáticamente si hay racha negativa |
| ✅ Kelly fraccional 20% | [`settings.yaml`](config/settings.yaml) | Máx 1.3x en sizing, ultra conservador |
| ✅ Sesión 15:00-21:00 | [`settings.yaml`](config/settings.yaml) | Horario de mayor liquidez en XRP |

### Por qué solo XRPUSDT

| Símbolo | ¿Viable con $50? | Razón |
|---------|:-:|-------|
| **XRPUSDT** | ✅ **SÍ** | ~$0.50-0.70/coin → puedes comprar ~70-100 unidades con $50. MinNotional ~$5-10. Spread ajustado. |
| ETHUSDT | ❌ NO | ~$2,800/coin → minNotional ~$30-50. Comisión relativa: ~$0.10-0.34 por trade. |
| DOGEUSDT | ❌ NO | Aunque es barato, tiene alta volatilidad + spread amplio. El bot pierde en DOGE con $50. |

> ⚠️ **Regla de oro:** Necesitas capital para comprar AL MENOS 10 unidades del activo. Con XRP a ~$0.60, puedes comprar ~83 unidades. Con ETH a ~$2,800, solo ~0.018 unidades — insuficiente.

---

## 🧪 Configuración Inicial (Binance Testnet)

### Paso 1: Crear API Keys en Binance Testnet

1. Ve a [Binance Testnet](https://testnet.binance.vision/)
2. Inicia sesión con tu cuenta de GitHub
3. Ve a **API Management** → **Create API Key**
4. Guarda tu `API Key` y `API Secret`

### Paso 2: Configurar secrets.env

Edita [`config/secrets.env`](config/secrets.env):

```bash
BINANCE_API_KEY=TU_API_KEY_DE_BINANCE_TESTNET
BINANCE_API_SECRET=TU_API_SECRET_DE_BINANCE_TESTNET
TESTNET=true                     # ← Esto es CRUCIAL al inicio
SYMBOL=XRPUSDT
```

### Paso 3: Verificar settings.yaml

Revisa [`config/settings.yaml`](config/settings.yaml) — confirma que:

```yaml
exchange:
  testnet: true   # ← Debe ser true para testnet

paper_trading:
  enabled: true   # ← Debe ser true (doble capa de seguridad)
  initial_balance: 50
```

> **Doble capa de seguridad:** `testnet: true` + `paper_trading: enabled = true`. Así pruebas primero contra la testnet de Binance con ejecución simulada.

### Paso 4: Obtener fondos Testnet

La Testnet de Binance te da **10,000 USDT virtuales** automáticamente. No necesitas fondos reales.

### Paso 5: Iniciar el bot en modo Paper (Testnet)

```bash
cd /home/oruetrash/dev/trading_bot
pip install -e .
python3 -m bot.monitor
```

Ve al dashboard: `http://localhost:8502`

### Paso 6: Período de Validación (¡OBLIGATORIO!)

Ejecuta el bot en **Paper + Testnet** durante **1-2 semanas completas** antes de pasar a mainnet.

**Criterios para pasar a mainnet:**
- ✅ Mínimo 15-20 trades ejecutados
- ✅ Win rate ≥ 50%
- ✅ Profit factor ≥ 1.2
- ✅ Drawdown máximo ≤ 5%
- ✅ Sin errores de conexión o API

---

## 🔴 Configuración Mainnet

### Paso 1: Crear API Keys en Binance REAL

1. Ve a [Binance.com](https://www.binance.com) → **API Management**
2. Crea una nueva API Key para futuros
   - Habilita **Enable Futures**
   - Restringe solo a trading (sin retiros)
3. Guarda tu `API Key` y `API Secret`

### Paso 2: Depositar $50 en Binance Futures

En la app/web de Binance:
1. Ve a **Wallet** → **Futures** → **Transfer**
2. Transfiere **$50 USDT** de Spot a USDⓈ-M Futures

### Paso 3: Actualizar secrets.env

```bash
BINANCE_API_KEY=TU_API_KEY_REAL
BINANCE_API_SECRET=TU_API_SECRET_REAL
# Si definís TESTNET, debe coincidir con exchange.testnet de settings.yaml.
# TESTNET=false
SYMBOL=XRPUSDT
```

Y en `config/settings.yaml`:

```yaml
exchange:
  testnet: false   # ← Mainnet
```

### Paso 4: Iniciar en modo Paper (Mainnet) — 1ª Semana

```yaml
# settings.yaml
paper_trading:
  enabled: true    # El bot ve el mercado real pero NO opera de verdad
```

Así validas que el bot lee bien los precios reales, sin arriesgar capital.

### Paso 5: INICIO REAL — Desactivar Paper Trading

```yaml
# settings.yaml
paper_trading:
  enabled: false   # ← Ahora Sí, el bot opera con dinero real
```

Además, para mainnet real el bot exige confirmación explícita:

```bash
# config/secrets.env
LIVE_TRADING_CONFIRMED=true
```

Sin esta variable, sin API keys válidas o sin balance real, el bot **no arranca**.

---

## 📈 Estrategia de Trading para $50

### Filosofía

Con $50 NO puedes permitirte errores. Cada trade cuenta.

- **1-2 trades por día** es normal. No fuerces entradas.
- **0.6% de riesgo por trade** = $0.30 por trade.
- **Ganancia neta esperada por trade ganador:** ~$0.60-1.20 (dependiendo si llega a TP1 o TP2).
- **Pérdida máxima por trade:** ~$0.30 (stop loss).

### Perfil de Trade Ideal

```
✅ Tendencia fuerte (ADX > 25)
✅ Pullback nítido hacia EMA20
✅ RSI en zona extrema (< 25 para LONG, > 70 para SHORT)
✅ Patrón de vela confirmatorio (pin bar, engulfing)
✅ Score ≥ 80
✅ Volumen sobre media (> 1.5x)
```

### Horario óptimo

| Horario (PYT) | Actividad |
|---------------|-----------|
| 14:00-14:45 | Preparación: revisar noticias, gráfico 1h |
| 15:00-17:00 | **Sesión principal** — mayor liquidez, mejores setups |
| 17:00-19:00 | Moderado — pueden aparecer setups buenos |
| 19:00-21:00 | Cierre de sesión — reducir tamaño si operas |
| 21:00+ | NO operar — spreads más amplios |

---

## 🎮 Guía de Operación Diaria

### Antes de Iniciar el Bot

```bash
# 1. Verificar que la configuración es correcta
python -c "from config.loader import validate_config; validate_config(raise_on_error=True)"

# 2. Iniciar el bot
python3 -m bot.monitor

# 3. Abrir dashboard
# Ve a http://localhost:8502
```

### Dashboard: Qué Mirar

| Indicador | Señal |
|-----------|-------|
| **Balance** | Debe mantenerse estable. Pérdidas de $0.30/día son normales en racha negativa. |
| **Trades Hoy** | Si ves >4 trades/día, algo está mal — el bot está sobreoperando. |
| **Score de señales** | Todas deben ser ≥ 80. Si ves señales con score < 80, revisa MIN_SCORE. |
| **Drawdown** | No debe superar 3% diario ($1.50). Si supera 5%, DETÉN el bot. |
| **SLs consecutivos** | Si llegas a 2 stops seguidos, el bot se detiene solo (por diseño). |

### Cuándo DETENER el bot manualmente

- ❌ **Drawdown > 5%** ($2.50 de pérdida) — proteger capital
- ❌ **3 pérdidas consecutivas** — algo cambió en el mercado
- ❌ **Noticias importantes** (CPI, FOMC, anuncios SEC sobre crypto)
- ❌ **El bot muestra errores raros de API** — revisar conexión

### Cuándo REINICIAR después de una pausa

- ✅ Drawdown recuperado (balance ≥ capital inicial)
- ✅ Pasaron al menos 2 horas desde la última pérdida
- ✅ El mercado muestra tendencia clara

---

## 💰 Money Management

### Tabla de Riesgo por Capital

| Capital | Risk/Trade | $ por trade | SL ($) | Win necesario para cubrir 1 loss |
|---------|-----------|-------------|--------|----------------------------------|
| $50 | 0.6% | $0.30 | ~$0.30 | 1 trade ganador (TP1=2.0RR) |
| $75 | 0.8% | $0.60 | ~$0.60 | 1 trade ganador |
| $100 | 1.2% | $1.20 | ~$1.20 | 1 trade ganador |
| $200+ | 1.5% | $3.00+ | ~$3.00 | 1 trade ganador |

### Fasificación Automática (Auto-Phase)

El bot ajusta automáticamente riesgo, RR y SL según tu balance:

| Fase | Capital | Risk | TP1-RR | TP2-RR | Apalancamiento |
|------|---------|------|--------|--------|---------------|
| 0 | $0-100 | 0.6% | 2.0 | 3.0 | 1x |
| 1 | $100-200 | 1.2% | 2.0 | 3.0 | 1x |
| 2 | $200-500 | 1.5% | 2.0 | 3.0 | 1x |
| 3 | $500-1500 | 1.2% | 1.8 | 3.0 | 2x |
| 4 | $1500+ | 1.0% | 1.5 | 3.0 | 3x |

> ⚠️ **Sin apalancamiento hasta $500.** El apalancamiento magnifica ganancias... y pérdidas.

### Kelly Criterion

El sizing usa **Kelly fraccional al 20%** (ultra conservador):

- **Ganancias consecutivas:** Aumenta ligeramente el tamaño (+3% por win, máx +15%)
- **Pérdidas consecutivas:** Reduce drásticamente (-10% por loss)
- **Máximo multiplicador:** 1.3x del tamaño base
- **Modo bootstrap (<30 trades):** Máx 1.1x

---

## 📊 Qué Esperar (Rentabilidad Realista)

### Proyección $50 → $100

| Métrica | Pesimista | Realista | Optimista |
|---------|-----------|----------|-----------|
| Trades/semana | 5 | 8 | 12 |
| Win rate | 40% | 55% | 65% |
| Profit factor | 0.9 | 1.3 | 1.8 |
| Ganancia/semana | -$1.50 | +$2.40 | +$5.40 |
| Tiempo a $100 | ❌ Nunca | ~21 semanas | ~9 semanas |

> Los números realistas suponen 8 trades/semana, 55% WR, RR promedio ~1.8 (entre TP1 y TP2).

### Escenario Realista Detallado

| Semana | Capital | Trades | Ganados | Perdidos | PnL | Balance |
|--------|---------|--------|---------|----------|-----|---------|
| 1 | $50.00 | 8 | 4 | 4 | +$0.60 | $50.60 |
| 2 | $50.60 | 8 | 5 | 3 | +$1.82 | $52.42 |
| 3 | $52.42 | 7 | 4 | 3 | +$0.94 | $53.36 |
| 4 | $53.36 | 9 | 5 | 4 | +$0.96 | $54.32 |
| 8 | ~$58 | - | - | - | - | - |
| 12 | ~$64 | - | - | - | - | - |
| 16 | ~$72 | - | - | - | - | - |
| 20 | ~$82 | - | - | - | - | - |
| 24 | ~$100 | 🎯 | - | - | - | **META** |

> **6 meses para duplicar** es un objetivo realista. No esperes milagros de un día para otro.

### Por qué NO esperar ganancias rápidas

1. **Con $50, el 0.6% de riesgo = $0.30 por trade.** Una ganancia de TP1 (2.0RR) = $0.60 neto.
2. **Binance cobra ~$0.04 por trade** (entrada + salida) en comisiones.
3. **Necesitas ~42 trades ganadores** (netos) para duplicar tu capital.
4. **A 8 trades/semana con 55% WR**, eso toma ~5-6 meses.

---

## 🔧 Solución de Problemas

| Problema | Causa | Solución |
|----------|-------|----------|
| El bot no abre trades | MIN_SCORE muy alto (80) | Bajar a 75 si hay <5 señales/semana |
| Micro-pérdidas frecuentes | Breakeven offset mal configurado | Verificar `BREAKEVEN_OFFSET_PCT` en position_manager.py |
| Errores de API | API Key incorrecta | Revisar secrets.env y que la key esté activa en Binance |
| "Min notional" error | Cantidad muy pequeña para el símbolo | Solo opera XRPUSDT con $50 |
| Dashboard no carga | Puerto ocupado | Cambiar `port` en settings.yaml o matar proceso anterior |
| Pérdidas >3% en un día | Mercado volátil o mala racha | DETENER el bot, esperar 24h, revisar settings |
| El bot se detiene solo | 2 stops consecutivos activaron protección | Es normal. Esperar cooldown o reiniciar manualmente |
| Paper trading muestra resultados irreales | PaperExecutor no tiene slippage real | Verificar `slippage_pct: 0.0005` en settings.yaml |

### Logs: Dónde Mirar

```bash
# Log principal del bot
tail -f logs/bot.log

# Log de trades
cat logs/trade_journal.csv

# Log de backtest
cat logs/backtest_XRPUSDT_*.csv
```

### Comandos Útiles

```bash
# Verificar configuración
python -c "from config.loader import validate_config; msgs = validate_config(raise_on_error=False); print('\n'.join(msgs))"

# Backtest rápido
python scripts/backtest.py --symbol XRPUSDT --capital 50 --start 2026-04-01 --end 2026-05-01

# Ver balance actual del bot (si está corriendo)
curl http://localhost:8000/metrics 2>/dev/null | grep balance

# Matar el bot
pkill -f "python3 -m bot.monitor"
```

---

## 📏 Métricas de Éxito

### Semana 1-2 (Testnet)
- [ ] Bot se conecta a Binance Testnet sin errores
- [ ] Recibe datos de mercado en tiempo real
- [ ] Genera señales con score ≥ 80
- [ ] Abre posiciones en paper mode
- [ ] TP1 y TP2 se ejecutan correctamente
- [ ] Breakeven offset funciona (sin micro-pérdidas)
- [ ] Dashboard muestra datos correctos

### Semana 3-4 (Paper en Mainnet)
- [ ] Bot se conecta a Binance real
- [ ] Precios en tiempo real coinciden con Binance
- [ ] Señales tienen sentido con el mercado real
- [ ] Sin errores de rate limiting
- [ ] Drawdown ≤ 3%

### Semana 5+ (Mainnet Real)
- [ ] Balance no baja de $45 en ningún momento
- [ ] Profit factor ≥ 1.2 después de 20 trades
- [ ] Win rate ≥ 50%
- [ ] Sin intervención manual necesaria por >48h
- [ ] Drawdown máximo semanal < 5%

---

## ⚠️ ADVERTENCIA FINAL

> **NUNCA pongas dinero que no puedas permitirte perder.**
> 
> Este bot NO es un "hazte rico rápido". Es una herramienta de trading algorítmico.
> Con $50, estás en modo **aprendizaje y bootstrap**. La rentabilidad real comienza
> a verse cuando llegas a $200-500.
>
> **Si en algún momento el balance baja de $40, DETÉN el bot inmediatamente**
> y revisa todo desde cero. Proteger el capital es tu prioridad #1.

---

## 🔗 Referencias

| Recurso | Link |
|---------|------|
| Binance Testnet | https://testnet.binance.vision/ |
| Binance Futures (Real) | https://www.binance.com/en/futures |
| Dashboard del Bot | http://localhost:8502 |
| Documentación Técnica | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| Código Fuente | [`/bot`](bot/) |
| Configuración Completa | [`config/settings.yaml`](config/settings.yaml) |
