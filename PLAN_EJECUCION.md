# 🚀 Phoenix — Plan de evolución: ejecución 1-click (mainnet)

> Estado: **aprobado en conversación**, pendiente de implementación.
> El pivote es una decisión explícita del usuario: reintroducir ejecución
> (mainnet) con entrada 1-click + gestión automática del ciclo, en modo
> **dry-run primero** y luego live con ~$15 de prueba.

## 1. Decisiones aprobadas

| Tema | Decisión |
|---|---|
| Meta | Ejecución **real en mainnet** (sin pasar por testnet), ~$15 de prueba |
| Automatización | **1-click manual**: vos apretás "operar" y el bot coloca la orden con el tamaño del Kelly |
| Gestión del ciclo | **Automática**: entrada → SL → TP1 parcial (50–65%) → breakeven → trailing → TP2 |
| Modo inicial | **Dry-run** (riesgo cero): mismo ciclo, fills simulados sobre mainnet |
| Pase a live | **Flag** manual cuando el dry-run acumule N trades sin bugs |
| Kill switch | **Sí**: botón que cancela todo y aplana |
| Límites de riesgo | **Solo aviso** (no hard limits) |
| Tamaño | **Kelly manda**, sin techo duro |
| Keys | Solo en `config/secrets.env`, nunca en la UI |
| Cierres | `reduceOnly` obligatorio |

## 2. Viability check (F0) — resultado

Datos reales de Binance USDT-M (público, 2026-11-07):

- 522 perpetuos USDT-M; el universo del radar (vol 24h > $5M, top 60) = 60 pares.
- **Mínimo de orden (notional)** en el universo:
  - $5: **53 pares** (la gran mayoría de alts).
  - $20: ETHUSDT, BCHUSDT.
  - $75: BTCUSDT.
  - $7: BNBUSDT, UNIUSDT.

**Conclusión con $15 (isolated, 1–2x):**

- **Operable**: los ~53 pares con mínimo $5. Un SL de 2% en el mínimo arriesga
  ~$0.10 = **0.67% del capital** (dentro del 1–2%). Sí es viable.
- **NO operables**: BTCUSDT ($75 min), ETHUSDT/BCHUSDT ($20 min) salvo que fuerces
  leverage y te pases de riesgo. El radar rankea BTC/ETH arriba → los vas a
  tener que **saltar** para ejecución real.
- **Límite real = granularidad, no fees**: el mínimo $5 es el 33% del capital,
  así que caben ~2–3 posiciones simultáneas y cada una ocupa 1/3 de la cuenta.
  Los fees son despreciables a este tamaño (round-trip ~$0.005 sobre $5).
- **Corrección** de lo dicho antes: con $15 los fees **no** se comen la cuenta;
  el cuello de botella es el mínimo de orden y el pocas posiciones concurrentes.

**Recomendación**: live solo con pares de mínimo $5 (excluir BTC/ETH/BCH/BNB/UNI),
SL ajustado (~1.5–2%), isolated 1–2x.

## 3. Fases de implementación

- **F0 — Viability check** ✅ **hecho** (resultados arriba).
- **F1 — Capa de ejecución**: `bot/core/exchange_client.py` **ya tiene** casi todo
  (`place_limit_order`, `place_stop_market_order`, `set_stop_loss`,
  `update_stop_loss`, `set_trailing_stop`, `cancel_order`, `get_positions`,
  `set_leverage`, `set_margin_type`). Falta: verificar reduceOnly en todos los
  cierres, y un wrapper de "estado de posición" que consolide fill/exit.
- **F2 — Modo dry-run**: mismo ciclo, fills simulados sobre precios reales de
  mainnet, sin mandar órdenes. Sirve para validar el loop y el journaling.
- **F3 — Bridge Radar→Orden (1-click)**: botón "operar" en la tarjeta, tamaño
  Kelly, confirmación con reduceOnly.
- **F4 — Gestión de ciclo**: state machine persistente (entrada → SL → TP1 parcial
  → breakeven → trailing → TP2), que sobreviva reinicios (lo más delicado).
- **F5 — Journaling automático**: persistir fill/exit/PnL en SQLite/CSV y
  alimentar el Kelly solo (sin tocar "Historial de señales" a mano).
- **F6 — Seguridad**: kill switch (cancela todo y aplana), enforce de `TESTNET`
  vs `exchange.testnet`, keys fuera de la UI.
- **F7 — Tests sin red** (`pytest -m "not integration"`) + `flake8 bot/ --select=E9,F63,F7,F82` en cada fase.

## 4. Reglas no negociables (se mantienen del AGENTS.md)

- Keys privadas solo en `config/secrets.env`; nunca en la UI.
- `reduceOnly` en todo cierre (nunca una orden de cierre que abra posición inversa).
- Si `TESTNET` está definido en `secrets.env`, debe coincidir con `exchange.testnet`.
- Sin llamadas bloqueantes dentro de async (`asyncio.to_thread`).
- Cada fase entra con test de regresión.
