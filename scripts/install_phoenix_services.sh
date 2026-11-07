#!/usr/bin/env bash
# Instala Phoenix como servicios de usuario de systemd.
# Uso: ./scripts/install_phoenix_services.sh
#      ENABLE_DASHBOARD=0 ./scripts/install_phoenix_services.sh  (sin dashboard)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LINK_DIR="$HOME/.local/share/phoenix"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ENABLE_DASHBOARD="${ENABLE_DASHBOARD:-1}"

echo "📁 Repo: $REPO_DIR"
mkdir -p "$UNIT_DIR" "$APPS_DIR" "$(dirname "$LINK_DIR")"
ln -sfn "$REPO_DIR" "$LINK_DIR"
echo "🔗 Acceso estable: $LINK_DIR"

for unit in phoenix-bot phoenix-dashboard; do
    sed "s|@LINK_DIR@|$LINK_DIR|g" \
        "$REPO_DIR/deploy/systemd/$unit.service" \
        > "$UNIT_DIR/$unit.service"
    echo "🧩 Instalado: $UNIT_DIR/$unit.service"
done

systemctl --user daemon-reload
systemctl --user enable phoenix-bot.service
systemctl --user restart phoenix-bot.service

if [[ "$ENABLE_DASHBOARD" == "1" ]]; then
    systemctl --user enable phoenix-dashboard.service
    systemctl --user restart phoenix-dashboard.service
else
    systemctl --user disable --now phoenix-dashboard.service 2>/dev/null || true
fi

# Arrancar servicios al encender la notebook, sin necesidad de login gráfico.
if ! loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q "Linger=yes"; then
    loginctl enable-linger "$USER" 2>/dev/null || \
        echo "⚠️ No se pudo habilitar linger. Ejecutá: sudo loginctl enable-linger $USER"
fi

# Lanzador de escritorio para el workspace de terminales.
cat > "$APPS_DIR/phoenix-workspace.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Phoenix Workspace
Comment=Monitor de señales, logs y dashboard
Exec=ptyxis --working-directory="$LINK_DIR" -- bash -lc "exec '$LINK_DIR/scripts/phoenix_workspace.sh'"
Icon=utilities-terminal
Terminal=false
Categories=Development;
StartupNotify=true
EOF
chmod 644 "$APPS_DIR/phoenix-workspace.desktop"
update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true

# Comando corto: phoenix start|stop|status|logs|dashboard
mkdir -p "$HOME/.local/bin"
ln -sf "$REPO_DIR/scripts/phoenix_ctl.sh" "$HOME/.local/bin/phoenix"

echo
echo "✅ Servicios instalados."
echo "   Monitor:   systemctl --user status phoenix-bot"
echo "   Dashboard: http://127.0.0.1:8502"
echo "   Workspace: buscá 'Phoenix Workspace' en el menú de aplicaciones"
echo
systemctl --user --no-pager --lines=5 status phoenix-bot.service || true
