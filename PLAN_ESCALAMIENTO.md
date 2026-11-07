# 📈 Phoenix — Plan de escalamiento (modo grow-only)

> **Enfoque:** todo lo ganado se reinvierte. Micro-aportes **variables y
> opcionales** (sin monto fijo) como acelerador. Horizonte **largo, sin fecha
> dura**: los $200–300/mes son realistas cuando el capital llegue a ~$4–6k.

## 1. Reglas del modo grow-only

1. **Todo el PnL se reinvierte** (no se retira nada hasta la meta).
2. **Micro-aportes variables**: metés lo que puedas, cuando puedas. No es un
   compromiso fijo.
3. **Los aportes se registran aparte del PnL** (columna `deposit` en el journal),
   para no contaminar el win-rate / profit factor con depósitos.
4. **Antes de probar el edge (Gate A), aportes mínimos o nulos**: estás
   validando, no escalando. Escalar una hipótesis sin probar = escalar pérdidas.
5. **Después del Gate A, cada aporte sube el capital y te mueve de etapa
   mecánicamente** (la etapa la define el balance, no el calendario).

## 2. Escalera por capital (mecánica: la define el balance)

| Etapa | Capital | Riesgo/trade | Pares | Lev | Stop diario |
|---|---|---|---|---|---|
| **0** | $15–$200 | 2% | mín $5 (alts) | 1–2x | −3% |
| **1** | $200–$1.000 | 1.5% | mín $5 | 1–2x | −3% |
| **2** | $1.000–$4.000 | 1% | + BTC/ETH | 2–3x | −2.5% |
| **3** | $4.000+ | 0.75% | selectivo | 1–2x | −2% |

- "Más al principio, menos después": 2% → 1.5% → 1% → 0.75%. **Nunca más de 2%.**
- La etapa **sube o baja sola** según el balance actual (incluye aportes y PnL).

## 3. Gate de edge probado (lo que te habilita a seguir escalando)

Subís de "validando" a "escalando" (y podés inyectar más tranquilo) cuando:
- ≥ **40 trades** cerrados.
- **Win-rate ≥ 35%** (con TP parcial 50–65% en 1R).
- **Profit factor ≥ 1.3**.
- **PnL > 0** consistente (no por un outlier).

## 4. Freno (lo que te para)

- **Stop diario** (−3% etapas 0–1, −2.5% etapa 2, −2% etapa 3). Si se toca, se
  acaba el día.
- **2 stops diarios en una semana** → bajás de etapa (o parás una semana) y
  revisás qué falló.

## 5. Hitos (por edge y capital, no por fecha)

| Hito | Qué es | Cuándo |
|---|---|---|
| H1 | Loop validado (dry-run + live sin bugs) | F1–F6 del plan de ejecución |
| H2 | Edge probado (Gate A) | 40 trades con WR/PF ok |
| H3 | Capital $500 | compounding (+ aportes si los hubo) |
| H4 | Capital $1.000 | idem |
| H5 | Capital $4–6k → zona $200–300/mes | idem |

## 6. La matemática (para expectativas sanas)

- Compounding puro $15 → $5.000: ~10 años al 5%/mes, ~5 años al 10%/mes.
- Con micro-aportes variables, el tiempo se acorta mucho (ej. $50/mes te acerca
  ~45× más rápido que el puro en los primeros 2 años).
- **Los primeros 12–18 meses son para validar el edge y la automatización**, no
  para ganar plata. El $ es trivial; el **%** es lo que escala.

## 7. Recomendaciones (decime si no estás de acuerdo)

- **VPS 24/7**: pasamos cuando llegues a **Etapa 1 ($200)**. Antes no tiene
  sentido pagar un server por $15.
- **Fases**: armo una **escalera nueva limpia para el radar** (la de arriba) en
  vez de reusar las `phases` legacy de `settings.yaml` (arrancan en $50 y mezclan
  conceptos del monitor viejo).
