"""Configuración global de pytest: filtros de warnings y fixtures compartidas."""
import warnings

# Silenciar DeprecationWarning de librerías externas que se disparan
# durante la importación (antes de que pyproject.toml filterwarnings tome efecto).
# websockets >=16 deprecó WebSocketClientProtocol, y python-binance aún lo importa.
warnings.filterwarnings("ignore", category=DeprecationWarning, module="websockets")
warnings.filterwarnings("ignore", category=DeprecationWarning, module="binance")
