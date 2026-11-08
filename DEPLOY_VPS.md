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
ssh -L 8502:127.0.0.1:8502 root@216.238.124.144
```

Dejá esa terminal abierta y entrá a **http://localhost:8502**.

## Importante: cuándo arranca el radar

- Streamlit **no** ejecuta el radar hasta que alguien abre el dashboard (una sesión).
- Una vez abierto, el scanner **sigue corriendo aunque cierres la pestaña**, hasta
  que se reinicie el proceso/VPS.
- **Después de reiniciar el VPS, abrí el dashboard una vez** para reanudar el radar.
  (Para LIVE conviene un runner headless sin Streamlit; pendiente.)

## Comandos útiles (en el VPS)

```bash
systemctl status phoenix-radar      # estado
systemctl restart phoenix-radar     # reiniciar
journalctl -u phoenix-radar -f      # logs en vivo
```

## Actualizar código

```bash
cd /opt/phoenix && git pull \
  && ./venv/bin/pip install -r requirements.txt \
  && systemctl restart phoenix-radar
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
