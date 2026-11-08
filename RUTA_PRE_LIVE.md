# 🗺️ Ruta antes de pasar a LIVE

> Objetivo: llegar a mainnet con un edge **medido**, la automatización **probada**
> y las protecciones **validadas**. No se saltea ninguna fase.

## Fase 1 — Validar el edge en paper (2–4 semanas)

- Meta: **≥40 trades**, **win-rate ≥35%**, **profit factor ≥1.3**, **PnL > 0**.
- Abrí 1–3 trades por día con el botón **"Operar (paper)"**. Nada a mano.
- No toques el "Historial" (ya no existe; el paper registra solo).
- Mirá en el panel Paper: curva de equity, PnL por trade, win-rate y etapa.
- **No** cambies `sl_distance_pct` ni los umbrales a mitad de medición.

## Fase 2 — Validar la automatización (sin plata)

- **Timeout**: dejá un trade lateral y confirmá que cierra solo a las 2h (`TIMEOUT`).
- **Kill switch**: con trades abiertos, apretá "🛑 Cerrar TODO" y verificá que cierra todo.
- **Persistencia**: cerrá el dashboard con trades abiertos y reabrilo → los trades deben recuperarse.
- **Notificaciones**: confirmá que te llega abrir, cerrar, y el estado por Telegram.

## Fase 3 — Testnet (ejecución real, sin plata)

- En `config/secrets.env`: keys de **testnet** (`BINANCE_API_KEY`/`SECRET`).
- `config/settings.yaml`: `exchange.testnet: true` y `scanner.dry_run: false`.
- Abrí **1 trade chico** y verificá: entra, coloca **SL + TP1 + TP2**, mueve SL a
  **breakeven** al llenarse el TP1, y cierra.
- Probá el **kill switch** aplanando en testnet.
- ⚠️ En testnet los precios **no** coinciden con las señales de mainnet: sirve
  para probar la **mecánica**, no el edge.

## Fase 4 — Mainnet, tamaño mínimo

- `exchange.testnet: false` + keys de **mainnet**.
- Empezá con **$15**, **1 trade a la vez**, y **bajá el riesgo** (ver abajo).
- Revisá el primer trade **manualmente en Binance**: fill vs señal, fees, SL/TP.
- Si algo no cuadra, volvés a paper/testnet antes de seguir.

## ✅ Checklist pre-live

- [ ] 40 trades paper con los gates cumplidos.
- [ ] Timeout + kill switch + persistencia probados.
- [ ] Telegram funcionando (abrir/cerrar/estado).
- [ ] Capa live validada en testnet (entrada, SL, TP1, breakeven, cierre).
- [ ] Keys **solo** en `config/secrets.env` (nunca en la UI ni en el repo).
- [ ] `max_risk_pct` bajado a **1–2%** (el 10% es solo para exagerar en paper).
- [ ] VPS o máquina que quede prendida 24/7 (la notebook no alcanza).

## Recomendaciones

1. **No pases a live con un gate fallando.** El edge se gana con datos, no con ansiedad.
2. **Bajá el riesgo al pasar a real.** El 10% por trade en paper está para ver movimiento;
   en mainnet un SL de 10% te liquida la cuenta en pocas rachas.
3. **1 símbolo por vez** en live: la reconciliación asume un trade por símbolo.
4. **Mantené paper en paralelo** los primeros días de live para comparar.
5. **No retires** hasta validar varias semanas; todo se reinvierte.
6. **Revisá el primer trade live a mano**: si el fill/stop no coincide, pará.
7. **Kill switch siempre a mano** antes de dormir o salir.
8. Guardá capturas de Binance + las notificaciones: te sirven de auditoría.

## Estado actual

- Paper: ✅ listo (loop, journaling, capital y riesgo automáticos, timeout, kill switch, stats, Telegram).
- Live: 🟡 implementado y testeado con mocks, **pendiente de validar en testnet/mainnet**.
