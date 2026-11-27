"""Canal de estado/comandos entre el motor headless y el dashboard.

El motor (``bot.radar_runner``) es el único dueño del scanner y de los trades:
escribe un snapshot JSON periódico y consume comandos de una cola de archivos.
El dashboard, cuando detecta el motor activo, corre en modo vista y usa este
módulo para leer el snapshot y encolar acciones (kill switch, abrir, auto).
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

_BASE = Path(__file__).resolve().parent.parent
DATA_DIR = _BASE / "data"
STATE_PATH = DATA_DIR / "radar_state.json"
LOCK_PATH = DATA_DIR / "radar.lock"
CMD_DIR = DATA_DIR / "radar_cmds"
HEARTBEAT_MAX_AGE = 6.0


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def write_state(payload: dict[str, Any]) -> None:
    _atomic_write_json(STATE_PATH, payload)


def read_state() -> Optional[dict[str, Any]]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return None


def engine_alive(max_age: float = HEARTBEAT_MAX_AGE) -> bool:
    state = read_state()
    if not state:
        return False
    try:
        return (time.time() - float(state.get("ts", 0.0))) <= max_age
    except (TypeError, ValueError):
        return False


def enqueue_command(payload: dict[str, Any]) -> None:
    CMD_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{time.time_ns()}_{os.getpid()}.json"
    _atomic_write_json(CMD_DIR / name, payload)


def drain_commands(limit: int = 100) -> list[tuple[Path, dict[str, Any]]]:
    if not CMD_DIR.exists():
        return []
    paths = sorted(CMD_DIR.glob("*.json"))[:limit]
    commands: list[tuple[Path, dict[str, Any]]] = []
    for path in paths:
        try:
            commands.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except (ValueError, OSError):
            command = {}
            commands.append((path, command))
    return commands


def try_acquire_lock() -> Optional[int]:
    """Toma el lock exclusivo del motor. Devuelve el fd o None si ya hay dueño."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def release_lock(fd: Optional[int]) -> None:
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
