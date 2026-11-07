# 📡 Phoenix — Guía completa de operación en papel

> **Modo actual: monitor-only.** Phoenix **NO ejecuta órdenes** y no guarda API
> keys privadas. Es un radar que te muestra anomalías del mercado y te calcula
> cuánto arriesgar. Vos operás a mano. Esta guía te explica **todo lo que tenés
> que hacer**, de principio a fin, para correr un período de paper y medir si la
> señal tiene edge.

---

## 0. Resumen ejecutivo (leé esto primero)

- Phoenix es un **radar de señales** para Binance USDⓈ-M Futures. Escanea
  cientos de pares, detecta anomalías (desbalance de order book, volume delta,
  funding extremo, liquidaciones) y te muestra **las mejores 3** con el tamaño
  sugerido.
- **No hay ejecución automática.** Desde el commit `fb75e64` se eliminó el motor
  de trading legacy (incluido `PaperExecutor`). Por eso "operar en papel" ahora
  significa **operar a mano y registrar el resultado** en el propio radar.
- El radar **aprende**: cada resultado WIN/LOSS que registrás ajusta el
  multiplicador de Kelly, que sube o baja el tamaño sugerido según tu historial.
- El objetivo del paper es simple: **juntar 30–50 trades en 1–2 semanas y medir
  win-rate + PnL** para decidir, con datos, si el edge existe.
- Nada de esto garantiza ganancias. El PLAYBOOK es tu contrato de disciplina.

---

## 1. Qué significa "correr paper" ahora

Antes existía un `PaperExecutor` que simulaba órdenes solo. **Ya no existe.** El
flujo de paper es manual y tiene dos variantes:

| Variante | Qué hacés | Dónde queda el registro |
|---|---|---|
| **A. Paper puro (recomendado)** | Leés la tarjeta, anotás entrada/SL/TP en un papel o planilla, y al cierre registrás WIN/LOSS + PnL en el radar | `data/scanner_signals.db` |
| **B. Testnet manual** | Repetís las órdenes a mano en la Testnet de Binance Futures (dinero ficticio) y después registrás el resultado en el radar | Testnet + `data/scanner_signals.db` |

Recomiendo **empezar por la variante A**. No necesitás cuenta, no hay fricción,
y es exactamente lo que el radar necesita para aprender. La variante B es útil
después, para practicar la ejecución (comisiones, parciales, breakeven) sin
arriesgar plata real.

> El radar lee **datos públicos de Mainnet** (`scanner.use_mainnet: true`), así
> que los precios son reales aunque tu operación sea ficticia.

---

## 2. Los dos componentes

El repo tiene dos herramientas distintas. No las confundas:

| Componente | Qué es | Para qué sirve |
|---|---|---|
| **Radar global** (`bot/scanner/` + dashboard) | Escanea cientos de pares por anomalías y rankea las mejores | **Esta es tu herramienta de paper** |
| **Monitor de 1 símbolo** (`bot/monitor.py`) | Estrategia legacy de lectura, evalúa UN símbolo (XRPUSDT) al cerrar cada vela 15m | Ficha de entrada/SL/TP de la estrategia vieja; **no es el paper** |

De acá en adelante, "el radar" = el dashboard Streamlit.

---

## 3. Requisitos e instalación

- **Python ≥ 3.12** y `pip`.
- Dependencias: `pip install -r requirements.txt`.
- Se recomienda un entorno virtual:

```bash
cd phoenix
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

- `config/secrets.env` **solo** si querés alertas por Telegram/Discord (el radar
  no necesita API keys). Copiá el ejemplo si querés alertas:

```bash
cp config/secrets.env.example config/secrets.env   # opcional
```

No hay nada más que configurar para arrancar: los umbrales ya vienen calibrados
en `config/settings.yaml` (sección `scanner`).

---

## 4. Cómo arrancar el sistema

### 4.1 Dashboard (recomendado para paper)

```bash
streamlit run bot/dashboard/app.py
```

Abrí `http://localhost:8501`. El radar se auto-refresca cada 3 segundos.

> Si lo levantás por systemd, el dashboard escucha en `http://127.0.0.1:8502`.
> Por Docker Compose, en `http://localhost:8501`.

### 4.2 Radar standalone (consola, sin UI)

```bash
python3 -m bot.scanner.market_scanner
```

Solo imprime logs de conexión y señales; no tiene el journal interactivo. Útil
para debug, pero **para paper usá el dashboard**.

### 4.3 Monitor de 1 símbolo (legacy, opcional)

```bash
python3 -m bot.monitor --once                          # una pasada
python3 -m bot.monitor --once --symbol BTCUSDT \
  --capital-pyg 100000 --fx 7300                        # con parámetros
python3 -m bot.monitor                                  # loop por vela 15m
```

### 4.4 Docker Compose

```bash
make up          # construye y levanta bot + dashboard
make logs        # logs del bot
make down        # detiene todo
```

### 4.5 Servicios systemd (para que quede corriendo)

```bash
./scripts/install_phoenix_services.sh   # instala y arranca bot + dashboard
phoenix start                           # arrancar
phoenix status                          # estado
phoenix logs                            # logs en vivo
phoenix report                          # win-rate y PnL del radar
phoenix open                            # workspace de terminales
```

> **Ojo con el uptime**: el radar corre solo mientras la notebook esté
> encendida. Para 24/7 real necesitás un VPS o dejar la máquina prendida con
> `loginctl enable-linger` (el instalador ya intenta habilitarlo).

---

## 5. La interfaz del Radar, control por control

Cuando abrís el dashboard ves, de arriba hacia abajo:

1. **4 controles** (cambiables en vivo, sin tocar config):
   - **💵 Capital (USDT)** — tu capital real disponible. Default `500`.
   - **🎚️ Riesgo/trade (%)** — fracción del capital a arriesgar. Default `1.0`.
     No pases de `2`.
   - **Nº señales** — cuántas tarjetas mostrar. Default `3`.
   - **Confianza mínima** — piso de confianza (0–90%). Default `40%`. Subila a
     50% si ves mucho ruido.

2. **Fila de métricas**:
   - `Pares rastreados` — tamaño del universo filtrado.
   - `Señales en buffer` — total emitidas desde el arranque.
   - `Eventos` — mensajes procesados (volumen de datos en vivo).
   - `Reconexiones` — cuántas veces se reconectó un socket (debería ser bajo).

3. **🔍 Diagnóstico técnico** (expander): contadores `evals/agg/depth/funding/force`
   y los "hits" por condición (`obi_hits`, `delta_hits`, `spike_hits`,
   `funding_hits`, `cascade_hits`). Sirve para ver **qué está disparando** tus
   señales. Si `last_error` aparece, algo falló.

4. **🚨 Mejores señales** — las tarjetas LONG/SHORT (ver sección 6).

5. **📋 Ruta para operar** — el checklist de 7 pasos (sección 10).

6. **📒 Historial de señales** — acá registrás WIN/LOSS/SKIP y PnL (sección 11).

7. **🔧 Qué se puede cambiar** — recordatorio de qué vive en `settings.yaml`.

---

## 6. Cómo leer una tarjeta de señal

Cada tarjeta muestra:

| Campo | Qué significa |
|---|---|
| **▲ LONG / ▼ SHORT** | Dirección de la hipótesis |
| **score** | Calidad compuesta 0–1 (más alto = mejor) |
| **motivos** | Frases legibles: qué condiciones se alinearon |
| **Confianza** | Seguridad del motor (0–100%) |
| **Precio** | Precio de referencia en el momento de la señal |
| **Arriesgar: $X USDT** | Cuánto perderías si el SL se toca (ya con Kelly) |
| **% del capital · Kelly ×N** | Riesgo en % y el multiplicador de Kelly actual |
| **motivos / liquidez / frescura** | Desglose del ranking (0–1, 0–100%, 0–100%) |

**Regla de oro:** operá solo tarjetas con **score alto, 2+ motivos y frescura
alta**. Si la tendencia 1h va en contra, descartá sin dudar.

---

## 7. Cómo decide una señal el motor de anomalías

Cuatro detectores se combinan en un score normalizado en `[-1, 1]`. Cada uno
aporta un peso y un motivo:

| Detector | Señal | Peso | Umbral (settings.yaml) |
|---|---|---|---|
| **Volume delta** | Diferencia compra/venta agresiva (taker) en ventana de 60s | 0.25 | `delta_extreme: 0.2` |
| **Spike de volumen** | Z-score de volumen total vs. su historia | 0.15 | `volume_zscore: 2.0` |
| **Order Book Imbalance (OBI)** | (bids − asks) / total del book parcial | 0.30 | `obi_extreme: 0.35` |
| **Funding extremo** | Funding muy alto → reversión SHORT; muy bajo → LONG | 0.15 | `funding_extreme: 0.0003` |
| **Cascada de liquidaciones** | Ráfaga de force-orders (nocional mínimo) | 0.30 | `liq_cascade_min_events: 3`, `liq_cascade_min_notional: 2000000` |

Reglas importantes:

- **Confianza** = `min(0.95, |score normalizado|)`. La dirección es LONG si el
  score es positivo, SHORT si es negativo.
- **Piso de emisión**: `min_confidence: 0.4` y `min_reasons: 2`. Si no se
  cumplen ambos, no hay tarjeta.
- **El spike solo sesga la dirección si el delta también es direccional** (un
  pico de volumen balanceado no se etiqueta como venta).
- **Cooldown 120s** por símbolo+dirección: la misma anomalía no re-emite la
  tarjeta cada pocos segundos.

---

## 8. Cómo se rankea (top-N)

El ranking combina 4 componentes en un score 0–1:

| Componente | Peso | Qué mide |
|---|---|---|
| Confianza | `w_confidence: 0.5` | La seguridad del motor |
| Motivos | `w_reasons: 0.25` | `min(1, nº_motivos/4)` |
| Liquidez | `w_liquidity: 0.15` | Percentil de volumen 24h dentro del universo |
| Frescura | `w_freshness: 0.1` | Decae exponencial (vida media `freshness_halflife_seconds: 300` = ~5 min) |

Las mejores `top_n` (default 3) se muestran. Por eso una tarjeta vieja baja de
puesto aunque su confianza sea alta.

---

## 9. Cómo se calcula "Arriesgar" (Kelly) y cómo aprende

La fórmula exacta es:

```
risk_pct  = base_risk_pct × confianza_escalada × kelly_mult
risk_usdt = capital × risk_pct

confianza_escalada = 0.5 + 0.5 × confidence
```

- Con confianza 40% → `confianza_escalada = 0.70`.
- Con confianza 95% → `0.975`.
- `kelly_mult` arranca en **1.0** y se ajusta con tus resultados.

### Cómo funciona el Kelly (`bot/core/kelly_sizing.py`)

| Regla | Valor |
|---|---|
| Mínimo de trades para ajustar | `min_trades: 5` (antes de eso, 1.0) |
| Ventana deslizante | `window_size: 20` trades |
| Kelly fraccional | `fractional: 0.25` (25% del Kelly completo) |
| Techo normal | `max_multiplier: 1.5` |
| Techo conservador (< 50 trades) | `conservative_max_multiplier: 1.2` |
| Piso | `min_multiplier: 0.3` |
| Racha | +3 wins → hasta +0.20; −2 losses → hasta −0.5 |

**En criollo:** al principio (menos de 50 trades) el sistema es conservador y
nunca te sugiere más de 1.2× tu riesgo base. Si ganás de forma consistente, el
multiplicador sube (hasta 1.5×); si perdés, baja (hasta 0.3×). Por eso es
**crítico registrar cada trade**: el tamaño aprende de la realidad.

---

## 10. El trade en papel, paso a paso

Seguí este procedimiento en cada señal. Es el PLAYBOOK hecho operativo.

### 10.1 Preparación (una sola vez)

1. Abrí el radar (`streamlit run bot/dashboard/app.py`).
2. Poné **Capital** = tu capital real en USDT.
3. **Riesgo/trade** = `1.0` (empezá ahí; nunca más de 2).
4. **Nº señales** = `3`, **Confianza mínima** = `40`.

### 10.2 Filtrar la señal

1. Esperá una tarjeta **LONG/SHORT con score alto y 2+ motivos**.
2. Revisá la **tendencia 1h** del símbolo (chart de Binance). Si va en contra
   de la dirección, **descartá**. Sin excepciones.
3. Confirmá en la **vela 15m** que no estás entrando justo en un pico.

### 10.3 Calcular la cantidad

```
cantidad = USDT_a_arriesgar ÷ distancia_al_SL
```

- `USDT_a_arriesgar` = el "Arriesgar: $X USDT" de la tarjeta.
- `distancia_al_SL` = |entrada − stop loss| en precio.
- **SL**: a `1.5–2× ATR(15m)` o en el nivel técnico más cercano.

### 10.4 "Entrar" (paper)

- **Variante A (puro)**: anotá fecha/hora, símbolo, dirección, precio de
  entrada, SL y TP. Ese es tu trade de papel.
- **Variante B (testnet)**: colocá una **orden límite** (menos comisiones que
  market) en la Testnet y poné el SL apenas llene.

### 10.5 Gestionar la salida

- **TP1**: cerrá el 50–65% al llegar a **1R** (`R = distancia entrada→SL`).
  Mové el SL a **breakeven**.
- **TP2**: dejá correr el resto hasta **2R** o trailing.
- **Hold máximo: 2 horas** (tope duro: 4 horas o el cierre de sesión
  14:00–22:00, lo que llegue primero).
- **Salí antes** si el motivo principal desaparece (desbalance revertido,
  volumen apagado, funding normalizado) o si el precio no se mueve a tu favor
  en los primeros 15–30 min.

> No conviertas un scalp en swing: si no se mueve, salí.

### 10.6 Cerrar y anotar

Al cerrar, calculá el PnL realizado en USDT (positivo = WIN, negativo = LOSS) y
pasá a la sección 11.

---

## 11. Registrar el resultado (WIN/LOSS/SKIP)

Este paso es **tan importante como operar bien**, porque es lo que alimenta el
Kelly y tus métricas.

1. Abrí **"📒 Historial de señales"** en el radar.
2. Vas a ver 3 métricas: **Resultados registrados**, **Win rate**, **PnL**.
3. Elegí la señal en el desplegable (`#id SYMBOL DIRECCIÓN (conf%)`).
4. Seleccioná el resultado:
   - **WIN** — operaste y ganaste → poné el PnL positivo.
   - **LOSS** — operaste y perdiste → poné el PnL negativo.
   - **SKIP** — la viste pero no la operaste (no afecta Kelly).
5. Clic en **"Registrar resultado"**.

Qué pasa por dentro:

- Se escribe en `data/scanner_signals.db` (SQLite).
- Si es WIN/LOSS, se llama a `risk.record_trade(pnl)` → **el Kelly se recalcula
  al instante** y el tamaño sugerido cambia en las próximas tarjetas.
- SKIP no toca el Kelly (no hubo trade).

> Atajo: `phoenix report` (o `./scripts/phoenix_ctl.sh report`) te imprime
> `Señales: N | WIN x | LOSS y | Win rate z% | PnL ±$`.

---

## 12. Medir: cuándo es concluyente

- Registrá **cada** señal operada, sin sesgo (no borres las pérdidas).
- Con **menos de 30–50 trades en 2 semanas**, el resultado no es
  estadísticamente concluyente y conviene extender el período.
- Mirá tres números en el Historial:
  1. **Win-rate** — con TP parcial 50–65% en 1R, un win-rate de 35–45% ya puede
     ser rentable (depende del ratio W/L).
  2. **PnL acumulado** — debe ser positivo de forma consistente, no por un
     outlier.
  3. **Kelly ×N** — si sube, el sistema "confía" más en tu historial; si baja,
     te está diciendo que achiques.

### Reglas de riesgo no negociables (del PLAYBOOK)

- **Máx 1–2% del capital por trade.**
- **Máx 3 trades abiertos a la vez.**
- **Stop diario: −5% del capital → pará.**
- **Sin excepciones** por "estoy seguro" o "voy a recuperar".
- Si dudás, no entres.

---

## 13. Configuración: qué podés tocar

Todo en `config/settings.yaml → scanner`. Los cambios requieren reiniciar el
radar (botón "🔄 Reiniciar radar" en Diagnóstico, o reiniciar el proceso).

| Parámetro | Default | Qué hace |
|---|---|---|
| `min_quote_volume_24h` | 5000000 | Filtra monedas con volumen 24h < $5M |
| `max_symbols` | 60 | Tope del universo rastreado |
| `depth_symbols_limit` | 30 | Cuántos pares (top volumen) llevan depth |
| `min_confidence` | 0.4 | Piso de confianza |
| `min_reasons` | 2 | Piso de motivos alineados |
| `top_n` | 3 | Cuántas tarjetas mostrar |
| `obi_extreme` | 0.35 | Sensibilidad del OBI |
| `volume_zscore` | 2.0 | Sensibilidad del spike |
| `delta_extreme` | 0.2 | Sensibilidad del delta |
| `funding_extreme` | 0.0003 | Sensibilidad del funding |
| `w_confidence / w_reasons / w_liquidity / w_freshness` | 0.5/0.25/0.15/0.1 | Pesos del ranking |
| `freshness_halflife_seconds` | 300 | Vida media de la frescura |

No toques los umbrales de riesgo/ranking sin anotar qué cambiaste y por qué:
eso contamina la medición del edge.

---

## 14. Caveats y limitaciones (sé honesto con esto)

1. **El edge no está validado todavía.** Todo lo anterior es infraestructura;
   el número que importa (win-rate real) no existe aún. Por eso el paper.
2. **Uptime limitado.** Corre solo con la notebook encendida. Para 24/7 real:
   VPS o `loginctl enable-linger` + servicios systemd.
3. **Los tests contaminan `logs/` y `data/`.** Si corrés `pytest` durante el
   período de paper, podés ensuciar los archivos de runtime. El `scanner_signals.db`
   del radar **no existe todavía** (está limpio); mantenelo así: no corras la
   suite de tests en medio de la medición, o hacé una copia de
   `data/scanner_signals.db` antes.
4. **Restos legacy en `data/` y `logs/`** (`bot_history.db`, `metrics.json`,
   `trade_journal.csv`, backtests, etc.) son del motor viejo que ya se eliminó.
   Podés borrarlos para mantener el workspace limpio; no los usa el radar.
5. **No hay ejecución ni keys privadas.** Si algún día querés reintroducir
   ejecución, es una decisión explícita tuya, con keys en `config/secrets.env`
   (nunca en la UI) y `reduceOnly` para cierres.
6. **`testnet` debe coincidir**: si definís `TESTNET` en `secrets.env`, tiene
   que coincidir con `exchange.testnet` o el monitor no arranca.

---

## 15. Cheatsheet de comandos

```bash
# Dashboard (paper)
streamlit run bot/dashboard/app.py            # → http://localhost:8501

# Radar standalone (consola)
python3 -m bot.scanner.market_scanner

# Monitor de 1 símbolo (legacy)
python3 -m bot.monitor --once
python3 -m bot.monitor --once --symbol XRPUSDT --capital-pyg 100000 --fx 7300

# Tests y gate de calidad
pytest -m "not integration"
flake8 bot/ --select=E9,F63,F7,F82

# Docker
make up / make logs / make down / make test

# systemd
./scripts/install_phoenix_services.sh
phoenix start|stop|status|logs|report|open
```

---

## 16. Resumen: tu checklist del primer paper

- [ ] `pip install -r requirements.txt` (en venv).
- [ ] Levantar el dashboard y configurar Capital / Riesgo 1% / 3 señales / 40%.
- [ ] Esperar una tarjeta con score alto, 2+ motivos y frescura alta.
- [ ] Confirmar tendencia 1h y vela 15m (descartar si va en contra).
- [ ] Calcular `cantidad = riesgo ÷ distancia al SL`, SL a 1.5–2× ATR.
- [ ] "Entrar" en papel (o testnet) y anotar entrada/SL/TP.
- [ ] TP1 50–65% en 1R → SL a breakeven; TP2 a 2R; hold máx 2h.
- [ ] Registrar WIN/LOSS + PnL en **📒 Historial de señales**.
- [ ] Repetir hasta 30–50 trades y revisar win-rate/PnL/Kelly.
- [ ] Respetar: máx 1–2% por trade, máx 3 abiertos, stop diario −5%.

---

> El radar no garantiza ganancias. Reduce el ruido y te da contexto, pero el
> edge se gana con constancia y control de riesgo, no con acertar siempre.
