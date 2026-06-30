# 🚀 Instalación y configuración — Phoenix

## Requisitos

- Python 3.10 o superior
- Git
- pip
- Conexión a internet (para conectarse a Binance)

## Instalación

1. Clonar el repositorio:

   git clone <url-del-repo>
   cd trading_bot

2. Crear entorno virtual:

python -m venv venv
source venv/bin/activate

3. Instalar dependencias:

pip install -r requirements.txt

4. Configurar credenciales (archivo config/secrets.env):

BINANCE_API_KEY=tu_api_key
BINANCE_API_SECRET=tu_api_secret
TESTNET=true
SYMBOL=XRPUSDT
TELEGRAM_TOKEN=opcional
TELEGRAM_CHAT_ID=opcional
DISCORD_WEBHOOK=opcional

5. Ejecutar tests para verificar que todo funciona:

pytest tests/

6. Iniciar el bot:

python -m bot.main

7. Iniciar el Dashboard

streamlit run bot/dashboard/app.py --server.port 8502

o usando el alias configurado

dashboard

8. Troubleshooting
Error "API Key not found": verificar que config/secrets.env tiene las credenciales correctas.

Error "ModuleNotFoundError": ejecutar pip install -r requirements.txt.

Error "Connection refused": verificar conexión a internet y acceso a Binance.
