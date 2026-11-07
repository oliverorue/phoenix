#!/bin/bash
# ============================================================
# Entrypoint para contenedor Docker de Phoenix
# ============================================================
set -e

# ── Crear directorios de datos/logs si no existen ──
mkdir -p /app/data /app/logs

# ── Sintaxis: docker-entrypoint.sh <service>
#   bot       → Inicia el monitor de señales (por defecto)
#   main      → Alias del monitor (compatibilidad)
#   dashboard → Inicia el dashboard Streamlit
#   test      → Ejecuta los tests
#   shell     → Bash interactivo
# ────────────────────────────────────────────────────────────

SERVICE="${1:-bot}"

case "$SERVICE" in
    bot|main)
        echo "📡 Iniciando Phoenix Signal Monitor (monitor-only)..."
        exec python -m bot.monitor
        ;;
    dashboard)
        echo "📊 Iniciando Dashboard Streamlit..."
        exec streamlit run /app/bot/dashboard/app.py \
            --server.port=8501 \
            --server.address=0.0.0.0 \
            --server.headless=true \
            --browser.gatherUsageStats=false
        ;;
    test)
        echo "🧪 Ejecutando tests..."
        shift
        exec python -m pytest "$@" /app/tests/
        ;;
    shell)
        echo "🐚 Shell interactivo"
        exec /bin/bash
        ;;
    *)
        echo "❌ Servicio desconocido: $SERVICE"
        echo "Usa: bot | main | dashboard | test | shell"
        exit 1
        ;;
esac
