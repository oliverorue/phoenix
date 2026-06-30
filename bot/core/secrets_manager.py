"""
Secrets Manager — Gestión segura de credenciales.

Soporta múltiples fuentes en orden de prioridad:
  1. Variables de entorno del sistema (Docker/K8s)
  2. HashiCorp Vault (KV v2)
  3. Archivo cifrado AES-256-GCM (secrets.env.enc)
  4. Archivo .env plano (desarrollo)

Características:
  - Lazy loading: los secretos se obtienen bajo demanda
  - Cache con TTL para Vault
  - Masking automático en logs y tracebacks
  - Validación de secretos requeridos al iniciar
  - Rotación de clave de cifrado local
"""

from __future__ import annotations

import os
import re
import json
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from loguru import logger


# ── Tipos públicos ───────────────────────────────────────────────────────────

class SecretSource(Enum):
    """Fuente de donde se obtuvo un secreto."""
    ENV = "env"           # Variable de entorno del sistema
    VAULT = "vault"       # HashiCorp Vault
    ENCRYPTED = "enc"     # Archivo cifrado AES-256-GCM
    DOTENV = "dotenv"     # Archivo .env plano
    UNKNOWN = "unknown"


class SecretNotFoundError(KeyError):
    """El secreto solicitado no existe en ninguna fuente."""


# ── Configuración ───────────────────────────────────────────────────────────

@dataclass
class SecretsConfig:
    """Configuración del gestor de secretos.

    Attributes:
        required_keys: Lista de claves que deben existir al validar.
        env_prefix: Prefijo opcional para variables de entorno (ej: "BOT_").
        vault_enabled: Si True, intenta Vault como fuente.
        vault_url: URL del servidor Vault.
        vault_token: Token de autenticación Vault (o desde env VAULT_TOKEN).
        vault_path: Ruta KV v2 (ej: "secret/data/trading-bot").
        vault_mount: Mount point del KV engine (ej: "secret").
        vault_ttl: TTL del caché de Vault en segundos.
        enc_file: Ruta al archivo cifrado (.enc).
        enc_key_env: Variable de entorno con la clave de cifrado.
        dotenv_path: Ruta al archivo .env plano.
        mask_chars: Cantidad de caracteres visibles al maskar.
    """
    required_keys: list[str] = field(default_factory=lambda: [
        "BYBIT_API_KEY",
        "BYBIT_API_SECRET",
    ])
    env_prefix: str = ""
    # Vault
    vault_enabled: bool = False
    vault_url: str = ""
    vault_token: str = ""
    vault_path: str = "secret/data/trading-bot"
    vault_mount: str = "secret"
    vault_ttl: int = 300
    # Encrypted file
    enc_file: str = ""
    enc_key_env: str = "SECRETS_ENCRYPTION_KEY"
    # Dotenv
    dotenv_path: str = "config/secrets.env"
    # Masking
    mask_chars: int = 3


# ── Excepciones ──────────────────────────────────────────────────────────────

class VaultAuthError(Exception):
    """Error de autenticación con Vault."""


class VaultUnavailableError(Exception):
    """Vault no está disponible (timeout/red)."""


class EncryptionError(Exception):
    """Error de cifrado/descifrado."""


# ── Masking ──────────────────────────────────────────────────────────────────

def mask_secret(value: str, visible_chars: int = 3) -> str:
    """Ofusca un secreto mostrando solo los primeros y últimos N caracteres.

    >>> mask_secret("abc123def456")
    'abc...456'
    >>> mask_secret("ab")
    'ab'
    >>> mask_secret("")
    ''
    """
    if not value:
        return ""
    if len(value) <= visible_chars * 2:
        return value
    return value[:visible_chars] + "..." + value[-visible_chars:]


_SECRET_PATTERNS = re.compile(
    r"(?i)(api[_-]?key|api[_-]?secret|secret|token|password|passwd|credential"
    r"|webhook|private[_-]?key)"
)


def is_likely_secret(key: str) -> bool:
    """True si el nombre de la clave sugiere que contiene un secreto."""
    return bool(_SECRET_PATTERNS.search(key))


# ── Vault Client ─────────────────────────────────────────────────────────────

class VaultClient:
    """Cliente HTTP mínimo para HashiCorp Vault (KV v2)."""

    def __init__(self, url: str, token: str, mount: str = "secret",
                 path: str = "data/trading-bot", ttl: int = 300) -> None:
        self._url = url.rstrip("/")
        self._token = token
        self._mount = mount
        self._path = path.lstrip("/")
        self._ttl = ttl
        self._cache: dict[str, tuple[Any, float]] = {}  # key → (value, expiry)
        self._session: Any = None

    def _import_requests(self) -> Any:
        """Importa requests bajo demanda (evita dependencia forzada)."""
        try:
            import requests
            return requests
        except ImportError:
            raise VaultUnavailableError(
                "Vault requiere la librería 'requests'. "
                "Instálala con: pip install requests"
            )

    def _ensure_session(self) -> Any:
        if self._session is None:
            requests = self._import_requests()
            self._session = requests.Session()
            self._session.headers.update({
                "X-Vault-Token": self._token,
                "Accept": "application/json",
            })
        return self._session

    def _is_cached(self, key: str) -> tuple[bool, Any]:
        if key in self._cache:
            value, expiry = self._cache[key]
            if expiry > datetime.now(timezone.utc).timestamp():
                return True, value
        return False, None

    def _set_cache(self, key: str, value: Any) -> None:
        expiry = datetime.now(timezone.utc).timestamp() + self._ttl
        self._cache[key] = (value, expiry)

    def _invalidate_cache(self) -> None:
        self._cache.clear()

    def get(self, key: str) -> Optional[str]:
        """Obtiene un secreto desde Vault. Usa caché si está fresco."""
        # Cache check
        cached, value = self._is_cached(key)
        if cached:
            return value

        # Fetch from Vault
        try:
            data = self._fetch_secrets()
        except VaultUnavailableError:
            return None

        # Buscar key en mayúsculas, minúsculas, o como esté
        secrets = data.get("data", {})
        for k, v in secrets.items():
            if k.upper() == key.upper():
                self._set_cache(key, str(v))
                return str(v)
        return None

    def get_all(self) -> dict[str, str]:
        """Obtiene todos los secretos desde Vault."""
        try:
            data = self._fetch_secrets()
        except VaultUnavailableError:
            return {}
        secrets = data.get("data", {})
        result: dict[str, str] = {}
        for k, v in secrets.items():
            result[k.upper()] = str(v)
        return result

    def _fetch_secrets(self) -> dict[str, Any]:
        """Obtiene secretos desde Vault KV v2."""
        session = self._ensure_session()
        # KV v2 path: /v1/{mount}/data/{path}
        api_path = f"/v1/{self._mount}/data/{self._path}"
        url = f"{self._url}{api_path}"

        try:
            resp = session.get(url, timeout=10)
        except Exception as e:
            raise VaultUnavailableError(f"Vault no disponible: {e}") from e

        if resp.status_code == 403:
            raise VaultAuthError("Token Vault inválido o sin permisos")
        if resp.status_code == 404:
            raise VaultUnavailableError(f"Ruta Vault no encontrada: {self._path}")
        if resp.status_code != 200:
            raise VaultUnavailableError(
                f"Vault respondió {resp.status_code}: {resp.text[:200]}"
            )

        # KV v2: data.data contiene los secretos
        payload = resp.json()
        return payload.get("data", {})

    def close(self) -> None:
        """Cierra la sesión HTTP."""
        if self._session is not None:
            self._session.close()
            self._session = None


# ── Encrypted File Store ─────────────────────────────────────────────────────

class EncryptedFileStore:
    """Almacén de secretos cifrados con AES-256-GCM.

    El archivo cifrado contiene un JSON con:
      { "version": 1, "secrets": { "KEY": "value", ... }, "nonce": "base64" }
    """

    ALGORITHM = "AES-256-GCM"
    NONCE_SIZE = 12  # 96 bits para GCM
    TAG_SIZE = 16    # 128 bits

    def __init__(self, file_path: str, encryption_key: Optional[str] = None) -> None:
        self._path = Path(file_path)
        self._encryption_key = encryption_key

    def _derive_key(self, raw_key: str) -> bytes:
        """Deriva una clave AES-256 de 32 bytes desde una clave arbitraria."""
        return hashlib.sha256(raw_key.encode("utf-8")).digest()

    def _import_crypto(self) -> Any:
        """Importa cryptography bajo demanda."""
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            return AESGCM
        except ImportError:
            raise EncryptionError(
                "Cifrado requiere 'cryptography'. "
                "Instálala con: pip install cryptography"
            )

    def exists(self) -> bool:
        """True si el archivo cifrado existe."""
        return self._path.is_file()

    def load(self, enc_key: Optional[str] = None) -> dict[str, str]:
        """Carga y descifra el archivo de secretos."""
        key = enc_key or self._encryption_key
        if not key:
            raise EncryptionError("Clave de cifrado no proporcionada")

        if not self.exists():
            return {}

        AESGCM = self._import_crypto()
        aesgcm = AESGCM(self._derive_key(key))
        raw = self._path.read_bytes()

        # Formato: nonce (12) + ciphertext + tag (16)
        nonce = raw[:self.NONCE_SIZE]
        ciphertext_with_tag = raw[self.NONCE_SIZE:]

        try:
            plaintext = aesgcm.decrypt(nonce, ciphertext_with_tag, None)
        except Exception as e:
            raise EncryptionError(f"Error descifrando {self._path}: {e}") from e

        try:
            data = json.loads(plaintext.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise EncryptionError(f"Formato inválido en archivo cifrado: {e}") from e

        return data.get("secrets", {})

    def save(self, secrets: dict[str, str], enc_key: Optional[str] = None) -> None:
        """Cifra y guarda secretos en el archivo."""
        key = enc_key or self._encryption_key
        if not key:
            raise EncryptionError("Clave de cifrado no proporcionada")

        AESGCM = self._import_crypto()
        aesgcm = AESGCM(self._derive_key(key))

        payload = json.dumps({
            "version": 1,
            "algorithm": self.ALGORITHM,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "secrets": secrets,
        }).encode("utf-8")

        nonce = os.urandom(self.NONCE_SIZE)
        ciphertext = aesgcm.encrypt(nonce, payload, None)

        # Formato: nonce (12) + ciphertext (incluye tag)
        self._path.write_bytes(nonce + ciphertext)

    def rotate_key(self, new_key: str, old_key: Optional[str] = None) -> None:
        """Re-cifra el archivo con una nueva clave."""
        current_key = old_key or self._encryption_key
        if not current_key:
            raise EncryptionError("Clave actual no disponible para rotación")

        secrets = self.load(enc_key=current_key)
        self.save(secrets, enc_key=new_key)

        if old_key is None:
            self._encryption_key = new_key


# ── Secrets Manager ──────────────────────────────────────────────────────────

class SecretsManager:
    """Gestor centralizado de secretos con múltiples fuentes.

    Orden de prioridad (mayor primero):
      1. Variables de entorno del sistema
      2. HashiCorp Vault (si configurado)
      3. Archivo cifrado AES-256-GCM (si existe)
      4. Archivo .env plano (fallback desarrollo)
    """

    def __init__(self, config: Optional[SecretsConfig] = None) -> None:
        self._config = config or SecretsConfig()
        self._vault: Optional[VaultClient] = None
        self._enc_store: Optional[EncryptedFileStore] = None
        self._dotenv_loaded = False
        self._dotenv_secrets: dict[str, str] = {}
        self._cache: dict[str, tuple[Optional[str], SecretSource]] = {}
        self._initialized = False

        logger.debug(
            f"🔐 SecretsManager inicializado "
            f"(vault={'on' if self._config.vault_enabled else 'off'}, "
            f"enc_file={'yes' if self._config.enc_file else 'no'}, "
            f"dotenv={self._config.dotenv_path})"
        )

    # ── Inicialización de fuentes ────────────────────────────────────────

    def _ensure_vault(self) -> None:
        """Inicializa el cliente Vault si está configurado."""
        if self._vault is not None:
            return
        if not self._config.vault_enabled:
            return
        if not self._config.vault_url:
            logger.warning("🔐 Vault habilitado pero sin vault_url")
            return

        token = self._config.vault_token or os.environ.get("VAULT_TOKEN", "")
        if not token:
            logger.warning("🔐 Vault habilitado pero sin token")
            return

        self._vault = VaultClient(
            url=self._config.vault_url,
            token=token,
            mount=self._config.vault_mount,
            path=self._config.vault_path,
            ttl=self._config.vault_ttl,
        )

    def _ensure_enc_store(self) -> None:
        """Inicializa el almacén cifrado."""
        if self._enc_store is not None:
            return
        enc_path = self._config.enc_file
        if not enc_path:
            return
        enc_key = os.environ.get(self._config.enc_key_env, "")
        self._enc_store = EncryptedFileStore(file_path=enc_path, encryption_key=enc_key)

    def _load_dotenv(self) -> None:
        """Carga el archivo .env plano directamente (sin tocar os.environ).

        Parseamos manualmente para mantener la distinción entre:
          - Variables de entorno reales (SecretSource.ENV)
          - Variables del archivo .env (SecretSource.DOTENV)
        """
        if self._dotenv_loaded:
            return
        dotenv_path = Path(self._config.dotenv_path)
        if not dotenv_path.is_file():
            self._dotenv_loaded = True
            return

        for line in dotenv_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            if key and value:
                self._dotenv_secrets[key] = value

        self._dotenv_loaded = True

    # ── API pública ──────────────────────────────────────────────────────

    def initialize(self) -> None:
        """Inicializa todas las fuentes. Llama una vez al arrancar."""
        if self._initialized:
            return
        self._ensure_vault()
        self._ensure_enc_store()
        self._load_dotenv()
        self._initialized = True

    def get(self, key: str, default: Optional[str] = None,
            source: Optional[SecretSource] = None) -> Optional[str]:
        """Obtiene un secreto por su nombre de clave.

        Args:
            key: Nombre del secreto (case-insensitive).
            default: Valor por defecto si no se encuentra.
            source: Si se especifica, solo busca en esa fuente.

        Returns:
            El valor del secreto o default si no existe.
        """
        # Cache hit (solo si no se especificó fuente)
        upper_key = key.upper()
        if source is None and upper_key in self._cache:
            value, _ = self._cache[upper_key]
            return value

        self.initialize()

        # 1. Variables de entorno (prioridad máxima)
        if source is None or source == SecretSource.ENV:
            env_key = f"{self._config.env_prefix}{key}"
            value = os.environ.get(env_key)
            if value is not None:
                self._cache[upper_key] = (value, SecretSource.ENV)
                return value

        # 2. Vault
        if source is None or source == SecretSource.VAULT:
            if self._vault is not None:
                try:
                    value = self._vault.get(key)
                    if value is not None:
                        self._cache[upper_key] = (value, SecretSource.VAULT)
                        return value
                except (VaultAuthError, VaultUnavailableError) as e:
                    logger.warning(f"🔐 Vault error para {key}: {e}")

        # 3. Archivo cifrado
        if source is None or source == SecretSource.ENCRYPTED:
            if self._enc_store is not None:
                try:
                    enc_secrets = self._enc_store.load()
                    for k, v in enc_secrets.items():
                        if k.upper() == upper_key:
                            self._cache[upper_key] = (v, SecretSource.ENCRYPTED)
                            return v
                except EncryptionError as e:
                    logger.warning(f"🔐 Error descifrando archivo: {e}")

        # 4. Dotenv
        if source is None or source == SecretSource.DOTENV:
            # dotenv ya cargó las variables en os.environ vía load_dotenv
            # Pero también puede estar en el dict parseado manualmente
            for k, v in self._dotenv_secrets.items():
                if k.upper() == upper_key:
                    self._cache[upper_key] = (v, SecretSource.DOTENV)
                    return v

        return default

    def get_required(self, key: str) -> str:
        """Obtiene un secreto o lanza SecretNotFoundError."""
        value = self.get(key)
        if value is None:
            raise SecretNotFoundError(
                f"Secreto requerido '{key}' no encontrado en ninguna fuente"
            )
        return value

    def get_all(self) -> dict[str, tuple[Optional[str], SecretSource]]:
        """Obtiene todos los secretos conocidos de todas las fuentes."""
        self.initialize()
        result: dict[str, tuple[Optional[str], SecretSource]] = {}

        # Env vars
        prefix = self._config.env_prefix
        for env_key, env_val in sorted(os.environ.items()):
            if prefix:
                if env_key.startswith(prefix):
                    clean_key = env_key[len(prefix):]
                    result[clean_key] = (env_val, SecretSource.ENV)
            else:
                if is_likely_secret(env_key):
                    result[env_key] = (env_val, SecretSource.ENV)

        # Vault
        if self._vault is not None:
            try:
                vault_secrets = self._vault.get_all()
                for k, v in vault_secrets.items():
                    result[k] = (v, SecretSource.VAULT)
            except (VaultAuthError, VaultUnavailableError):
                pass

        # Encrypted file
        if self._enc_store is not None:
            try:
                enc_secrets = self._enc_store.load()
                for k, v in enc_secrets.items():
                    result[k] = (v, SecretSource.ENCRYPTED)
            except EncryptionError:
                pass

        # Dotenv
        for k, v in self._dotenv_secrets.items():
            result[k] = (v, SecretSource.DOTENV)

        return result

    def validate(self) -> list[str]:
        """Verifica que todos los secretos requeridos existan.

        Returns:
            Lista de claves faltantes (vacía si todo ok).
        """
        missing: list[str] = []
        for key in self._config.required_keys:
            value = self.get(key)
            if value is None or value.strip() == "" or "TU_" in value:
                missing.append(key)
        return missing

    def get_source(self, key: str) -> SecretSource:
        """Obtiene la fuente de un secreto específico."""
        upper_key = key.upper()
        if upper_key in self._cache:
            _, source = self._cache[upper_key]
            return source

        # Forzar búsqueda para poblar caché
        self.get(key)
        if upper_key in self._cache:
            _, source = self._cache[upper_key]
            return source
        return SecretSource.UNKNOWN

    def reload(self) -> None:
        """Recarga todos los secretos desde las fuentes (invalida caché)."""
        self._cache.clear()
        if self._vault is not None:
            self._vault._invalidate_cache()
        logger.info("🔄 SecretsManager: caché invalidada, recarga completada")

    def mask(self, key: str, value: Optional[str] = None) -> str:
        """Retorna una versión maskada del secreto para logging.

        Si no se provee value, lo busca internamente.
        """
        if value is None:
            value = self.get(key) or ""
        if not value:
            return "<empty>"
        return mask_secret(value, self._config.mask_chars)

    def report(self) -> dict[str, Any]:
        """Genera un reporte de estado de los secretos (sin valores reales)."""
        self.initialize()
        status: dict[str, Any] = {
            "vault_configured": self._config.vault_enabled,
            "vault_connected": self._vault is not None,
            "enc_file_exists": self._enc_store.exists() if self._enc_store else False,
            "dotenv_loaded": self._dotenv_loaded,
            "secrets": {},
        }
        for key in self._config.required_keys:
            value = self.get(key)
            source = self.get_source(key)
            status["secrets"][key] = {
                "found": value is not None and "TU_" not in (value or ""),
                "source": source.value,
                "masked": self.mask(key, value),
            }
        return status

    def close(self) -> None:
        """Cierra conexiones (Vault)."""
        if self._vault is not None:
            self._vault.close()
            self._vault = None
        self._cache.clear()
        self._initialized = False


# ── Singleton global ─────────────────────────────────────────────────────────

_secrets_manager: Optional[SecretsManager] = None


def get_secrets_manager(config: Optional[SecretsConfig] = None) -> SecretsManager:
    """Obtiene o crea el singleton SecretsManager."""
    global _secrets_manager
    if _secrets_manager is None:
        _secrets_manager = SecretsManager(config=config)
    return _secrets_manager


def reset_secrets_manager() -> None:
    """Resetea el singleton (útil en tests)."""
    global _secrets_manager
    if _secrets_manager is not None:
        _secrets_manager.close()
    _secrets_manager = None
