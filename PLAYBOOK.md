# 📡 Phoenix — Playbook de operación manual

Este radar **no ejecuta órdenes**. Te muestra las mejores anomalías del mercado
para que vos operes a mano, con el tamaño ya calculado. Leelo una vez y seguilo
al pie de la letra; la disciplina es lo único que te protege.

---

## 1. Arrancar

```bash
streamlit run bot/dashboard/app.py
```

Abrí `http://localhost:8501`. Configurá en la pestaña Radar:

- **Capital (USDT)**: tu capital real disponible.
- **Riesgo/trade**: **1%** para empezar (nunca más de 2%).
- **Nº señales**: 3 (las mejores).
- **Confianza mínima**: 40% (subila a 50% si querés menos ruido).

---

## 2. Cómo leer una tarjeta

Cada tarjeta muestra:

| Campo | Qué significa |
|---|---|
| **LONG / SHORT** | Dirección de la hipótesis |
| **score** | Qué tan buena es (0–1). Más alto = mejor |
| **motivos** | Cuántas condiciones se alinearon (OBI, volumen, funding, liquidaciones) |
| **Confianza** | Seguridad del motor en esa dirección |
| **liquidez** | Percentil de volumen 24h (más = mejor para salir) |
| **frescura** | Si es reciente. Se enfría en ~5 min |
| **Arriesgar** | Cuántos USDT perder si el SL se toca (ya con Kelly) |

**Regla de oro: operá solo tarjetas con score alto, 2+ motivos y frescura alta.**
Si la tendencia 1h va en contra de la dirección, descartá sin dudar.

---

## 3. Cuánto tiempo estar en un trade

Estas señales son **scalps**: entrás y salís en **minutos a 2 horas**.

- **Entrar**: apenas ves la tarjeta (la anomalía se diluye en minutos).
- **Hold máximo**: **2 horas**. Tope duro: **4 horas** o el cierre de sesión
  (14:00–22:00), lo que llegue primero.
- **Salir antes** si el motivo principal desaparece (el desbalance se revierte,
  el volumen se apaga, el funding se normaliza) o si el precio no se mueve a tu
  favor en los primeros 15–30 min.

> No conviertas un scalp en un swing: si no se mueve, salí.

---

## 4. Entrada, SL y TP

1. **Confirmá en el gráfico 15m** que no estás comprando/vendiendo justo en el pico.
2. **Calculá cantidad**: `cantidad = USDT_a_arriesgar ÷ distancia_al_SL`.
3. **Entrá con orden límite** (menos comisiones que market).
4. **SL**: a **1.5–2× ATR(15m)** o en el nivel técnico más cercano. Pone el SL
   **apenas se llene la orden**.
5. **TP1**: cerrá el 50–65% al llegar a **1R**. Mové el SL a breakeven.
6. **TP2**: dejá correr el resto hasta **2R** o trailing.

`R = distancia entrada → SL`. Si arriesgás $0.30, 1R = $0.30, 2R = $0.60.

---

## 5. Reglas de riesgo (no negociables)

- **Máx 1–2% del capital por trade.**
- **Máx 3 trades abiertos a la vez.**
- **Stop diario**: si perdés **5% del capital en un día, pará.**
- **Sin excepciones** por "estoy seguro" o "voy a recuperar".
- Si dudás, no entres.

---

## 6. Después de cada trade

1. Abrí **"📒 Historial de señales"** en el Radar.
2. Elegí la señal y registrá el resultado: **WIN / LOSS** + el PnL real.
3. El **Kelly** se ajusta solo con tus resultados: sube el tamaño si venís bien,
   lo baja si venís mal.

---

## 7. Qué se puede afinar

Todo en `config/settings.yaml → scanner`:

| Parámetro | Qué hace |
|---|---|
| `min_confidence` / `min_reasons` | Piso de calidad de las tarjetas |
| `top_n` | Cuántas señales mostrar |
| `w_confidence / w_reasons / w_liquidity / w_freshness` | Pesos del ranking |
| `min_quote_volume_24h` | Filtra monedas con poco volumen |
| `obi_extreme / volume_zscore / delta_extreme / funding_extreme` | Sensibilidad del motor |

---

## ⚠️ Importante

El radar **no garantiza ganancias**. Reduce el ruido y te da contexto, pero el
edge se gana con constancia y control de riesgo. Antes de arriesgar plata real,
operá en papel 1–2 semanas y medí el win-rate desde el historial.
