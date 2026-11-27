# 🚀 Deploy en VPS (Phoenix 24/7)

Servidor de referencia: **Vultr, São Paulo (Brasil)** — Ubuntu 24.04, 1 vCPU / 1 GB.
Instalado y funcionando por primera vez el 2026-11-08.

> ⚠️ **Región crítica**: Binance **bloquea IPs de US y Canadá**
> (`Service unavailable from a restricted location`). Usá **Brasil (São Paulo)**
> o **Europa (Frankfurt/Amsterdam)**. Una VPS en Miami/USA **no** funciona.

## Qué quedó instalado

- Código en `/opt/phoenix` (git, repo público) + venv en `/opt/phoenix/venv`.
- `config/secrets.env` con Telegram (permisos `600`, solo root).
- **2 GB de swap** (1 GB de RAM es justo para Streamlit + scanner).
- Servicio systemd **`phoenix-radar.service`** → corre el dashboard en
  `127.0.0.1:8502` (que a su vez levanta el scanner/radar + paper/live).
- SSH **solo con clave** (`PasswordAuthentication no`), contraseña deshabilitada.

## Acceso al dashboard (túnel SSH)

El dashboard escucha solo en localhost del VPS. Desde tu notebook:

```bash
phx
```

Abre el túnel SSH y el navegador en un solo paso (Ctrl+C cierra el túnel).
El comando es un symlink a `scripts/phoenix_dashboard.sh`; el equivalente
manual sigue siendo:

```bash
ssh -L 8502:127.0.0.1:8502 root@216.238.124.144
```

y entrar a **http://localhost:8502**.

## Arquitectura: motor headless + dashboard

- **`phoenix-radar-engine.service`** corre `python -m bot.radar_runner`: scanner,
  paper/live y auto-entrada **sin Streamlit**, 24/7. Es el único que opera
  (toma el lock `data/radar.lock`).
- **`phoenix-radar.service`** corre Streamlit en `127.0.0.1:8502`. Si el motor
  está activo, el dashboard entra en **modo vista**: lee `data/radar_state.json`
  y envía comandos por `data/radar_cmds/` (abrir, kill switch, auto on/off).
- Ya no hace falta abrir el dashboard después de reiniciar el VPS: el motor
  arranca solo y sigue operando.

## Comandos útiles (en el VPS)

```bash
systemctl status phoenix-radar-engine   # motor (scanner + trades)
systemctl status phoenix-radar          # dashboard
systemctl restart phoenix-radar-engine  # reiniciar el motor
journalctl -u phoenix-radar-engine -f   # logs del motor en vivo
```

## Actualizar código

```bash
cd /opt/phoenix && git pull \
  && ./venv/bin/pip install -r requirements.txt \
  && systemctl restart phoenix-radar-engine phoenix-radar
```

## Backup del historial (desde tu notebook)

```bash
scp root@216.238.124.144:/opt/phoenix/data/paper_trades.db ./paper_trades.db
```

## Seguridad

- `config/secrets.env` en `600`; keys **nunca** en la UI ni en git.
- SSH key-only (la contraseña de root no sirve para entrar).
- No se abrió ningún puerto extra: el dashboard es local vía túnel SSH.
- Si querés cambiar la contraseña de root igual: `passwd` (opcional).

## Pasar a LIVE (más adelante)

1. Agregar `BINANCE_API_KEY` / `BINANCE_API_SECRET` en `config/secrets.env` (VPS).
2. En `config/settings.yaml`: `scanner.dry_run: false` y `exchange.testnet: false`.
3. Antes, validar en **testnet** con tamaño mínimo (ver `RUTA_PRE_LIVE.md`).
4. Para operación live 24/7 robusta, migrar el core a un **runner headless**
   (sin depender de tener abierta una sesión de Streamlit).
