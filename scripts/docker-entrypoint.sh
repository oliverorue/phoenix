#!/bin/bash
# ============================================================
# Entrypoint para contenedor Docker de Phoenix
# ============================================================
set -e

# ── Crear directorios de datos/logs si no existen ──
mkdir -p /app/data /app/logs

# ── Sintaxis: docker-entrypoint.sh <service>
#   bot       → Inicia el watchdog (por defecto)
#   main      → Inicia el bot directamente (sin watchdog)
#   dashboard → Inicia el dashboard Streamlit
#   test      → Ejecuta los tests
#   shell     → Bash interactivo
# ────────────────────────────────────────────────────────────

SERVICE="${1:-bot}"

case "$SERVICE" in
    bot)
        echo "🔥 Iniciando Watchdog de Phoenix..."
        exec python /app/bot/watchdog.py
        ;;
    main)
        echo "🔥 Iniciando Phoenix directamente (sin watchdog)..."
        exec python -m bot.main
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
