#!/bin/bash
# ============================================================
# Script de arranque de Phoenix — Signal Monitor (monitor-only)
# Uso: ./scripts/start.sh
# ============================================================

# Colores para la terminal
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # Sin color

echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}   🔥 Iniciando Phoenix - $(date '+%Y-%m-%d %H:%M:%S')${NC}"
echo -e "${GREEN}============================================================${NC}"

# Obtener el directorio raíz del proyecto
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR" || exit 1

# Activar entorno virtual
if [ -f "venv/bin/activate" ]; then
    echo -e "${YELLOW}🔧 Activando entorno virtual...${NC}"
    source venv/bin/activate
else
    echo -e "${RED}❌ Entorno virtual no encontrado. Ejecuta: python3 -m venv venv${NC}"
    exit 1
fi

# Verificar dependencias
if ! python3 -c "import yaml, dotenv, loguru" 2>/dev/null; then
    echo -e "${YELLOW}📦 Instalando dependencias...${NC}"
    pip install -r requirements.txt
fi

# Iniciar el monitor de señales (sin ejecución de órdenes)
echo -e "${GREEN}📡 Iniciando monitor de señales (monitor-only)...${NC}"
python3 -m bot.monitor

# Si el monitor se detiene, mostrar mensaje
echo -e "${YELLOW}⏹️ Monitor detenido - $(date '+%Y-%m-%d %H:%M:%S')${NC}"