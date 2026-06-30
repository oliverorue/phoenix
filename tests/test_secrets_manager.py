"""
Tests para el módulo de Secrets Manager.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Generator
from unittest.mock import patch

import pytest

from bot.core.secrets_manager import (
    SecretsManager,
    SecretsConfig,
    SecretSource,
    SecretNotFoundError,
    VaultClient,
    VaultUnavailableError,
    EncryptedFileStore,
    EncryptionError,
    mask_secret,
    is_likely_secret,
    get_secrets_manager,
    reset_secrets_manager,
)


# ── Tests de utilidades ──────────────────────────────────────────────────────

class TestMaskSecret:
    """Verifica la función mask_secret."""

    def test_masks_middle_chars(self) -> None:
        assert mask_secret("abc123def456") == "abc...456"

    def test_short_value(self) -> None:
        assert mask_secret("ab") == "ab"

    def test_empty_value(self) -> None:
        assert mask_secret("") == ""

    def test_exactly_double_visible(self) -> None:
        assert mask_secret("abcdef") == "abcdef"

    def test_custom_visible_chars(self) -> None:
        assert mask_secret("abcdefgh", visible_chars=2) == "ab...gh"


class TestIsLikelySecret:
    """Verifica la detección de nombres de clave que parecen secretos."""

    def test_api_key(self) -> None:
        assert is_likely_secret("BYBIT_API_KEY") is True

    def test_api_secret(self) -> None:
        assert is_likely_secret("API_SECRET") is True

    def test_token(self) -> None:
        assert is_likely_secret("TELEGRAM_TOKEN") is True

    def test_webhook(self) -> None:
        assert is_likely_secret("DISCORD_WEBHOOK") is True

    def test_password(self) -> None:
        assert is_likely_secret("DB_PASSWORD") is True

    def test_public_value(self) -> None:
        assert is_likely_secret("SYMBOL") is False

    def test_timeframe(self) -> None:
        assert is_likely_secret("TIMEFRAME_PRIMARY") is False


# ── Tests SecretsConfig ──────────────────────────────────────────────────────

class TestSecretsConfig:
    """Verifica los valores por defecto y personalizados."""

    def test_defaults(self) -> None:
        cfg = SecretsConfig()
        assert "BYBIT_API_KEY" in cfg.required_keys
        assert cfg.env_prefix == ""
        assert cfg.vault_enabled is False
        assert cfg.dotenv_path == "config/secrets.env"
        assert cfg.mask_chars == 3

    def test_custom_values(self) -> None:
        cfg = SecretsConfig(
            required_keys=["MY_KEY"],
            env_prefix="BOT_",
            vault_enabled=True,
            vault_url="http://vault:8200",
        )
        assert cfg.required_keys == ["MY_KEY"]
        assert cfg.env_prefix == "BOT_"
        assert cfg.vault_enabled is True
        assert cfg.vault_url == "http://vault:8200"


# ── Tests SecretsManager — Básico ────────────────────────────────────────────

class TestSecretsManagerBasic:
    """Verifica la funcionalidad básica del SecretsManager."""

    def setup_method(self) -> None:
        reset_secrets_manager()

    @pytest.fixture(autouse=True)
    def _cleanup(self) -> Generator:
        yield
        reset_secrets_manager()

    def test_get_returns_none_for_missing(self) -> None:
        sm = SecretsManager(config=SecretsConfig(dotenv_path=""))
        assert sm.get("NONEXISTENT_KEY") is None

    def test_get_returns_default(self) -> None:
        sm = SecretsManager(config=SecretsConfig(dotenv_path=""))
        assert sm.get("NONEXISTENT_KEY", default="fallback") == "fallback"

    def test_get_required_raises(self) -> None:
        sm = SecretsManager(config=SecretsConfig(dotenv_path=""))
        with pytest.raises(SecretNotFoundError):
            sm.get_required("NONEXISTENT_KEY")

    def test_get_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_MY_SECRET", "env-value")
        sm = SecretsManager(config=SecretsConfig(dotenv_path=""))
        assert sm.get("TEST_MY_SECRET") == "env-value"
        assert sm.get_source("TEST_MY_SECRET") == SecretSource.ENV

    def test_env_takes_priority_over_dotenv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BYBIT_API_KEY", "from-env")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write('BYBIT_API_KEY=from-dotenv\n')
            dotenv_path = f.name

        try:
            sm = SecretsManager(config=SecretsConfig(dotenv_path=dotenv_path))
            assert sm.get("BYBIT_API_KEY") == "from-env"
            assert sm.get_source("BYBIT_API_KEY") == SecretSource.ENV
        finally:
            os.unlink(dotenv_path)

    def test_cache_used_on_second_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CACHED_KEY", "value")
        sm = SecretsManager(config=SecretsConfig(dotenv_path=""))
        assert sm.get("CACHED_KEY") == "value"
        monkeypatch.delenv("CACHED_KEY")
        # Debe devolver el valor cachead (no consulta env de nuevo)
        assert sm.get("CACHED_KEY") == "value"

    def test_reload_invalidates_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RELOAD_KEY", "old")
        sm = SecretsManager(config=SecretsConfig(dotenv_path=""))
        assert sm.get("RELOAD_KEY") == "old"
        monkeypatch.setenv("RELOAD_KEY", "new")
        # Sin reload, debe devolver el valor cachead
        assert sm.get("RELOAD_KEY") == "old"
        # Con reload, debe capturar el nuevo
        sm.reload()
        assert sm.get("RELOAD_KEY") == "new"


# ── Tests SecretsManager — Dotenv ────────────────────────────────────────────

class TestSecretsManagerDotenv:
    """Verifica la carga desde archivo .env."""

    def setup_method(self) -> None:
        reset_secrets_manager()

    @pytest.fixture(autouse=True)
    def _cleanup(self) -> Generator:
        yield
        reset_secrets_manager()

    def test_loads_from_dotenv_file(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("DOTENV_KEY=dotenv-value\n")
            f.write("# comment\n")
            f.write("ANOTHER_KEY=another\n")
            dotenv_path = f.name

        try:
            sm = SecretsManager(config=SecretsConfig(dotenv_path=dotenv_path))
            assert sm.get("DOTENV_KEY") == "dotenv-value"
            assert sm.get("ANOTHER_KEY") == "another"
            assert sm.get_source("DOTENV_KEY") == SecretSource.DOTENV
        finally:
            os.unlink(dotenv_path)

    def test_empty_dotenv_returns_none(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("# solo comentarios\n")
            dotenv_path = f.name

        try:
            sm = SecretsManager(config=SecretsConfig(dotenv_path=dotenv_path))
            assert sm.get("ANY_KEY") is None
        finally:
            os.unlink(dotenv_path)

    def test_missing_dotenv_file(self) -> None:
        sm = SecretsManager(config=SecretsConfig(dotenv_path="/nonexistent/path/.env"))
        assert sm.get("ANY_KEY") is None


# ── Tests SecretsManager — Validate ──────────────────────────────────────────

class TestSecretsManagerValidate:
    """Verifica la validación de secretos requeridos."""

    def setup_method(self) -> None:
        reset_secrets_manager()

    @pytest.fixture(autouse=True)
    def _cleanup(self) -> Generator:
        yield
        reset_secrets_manager()

    def test_validate_returns_empty_when_all_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BYBIT_API_KEY", "real-key")
        monkeypatch.setenv("BYBIT_API_SECRET", "real-secret")
        sm = SecretsManager(config=SecretsConfig(dotenv_path=""))
        missing = sm.validate()
        assert missing == []

    def test_validate_returns_missing_keys(self) -> None:
        sm = SecretsManager(config=SecretsConfig(
            required_keys=["REQUIRED_1", "REQUIRED_2"],
            dotenv_path="",
        ))
        missing = sm.validate()
        assert "REQUIRED_1" in missing
        assert "REQUIRED_2" in missing

    def test_validate_detects_placeholder_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Valores como 'TU_API_KEY' deben considerarse faltantes."""
        monkeypatch.setenv("BYBIT_API_KEY", "TU_API_KEY_AQUI")
        sm = SecretsManager(config=SecretsConfig(dotenv_path=""))
        missing = sm.validate()
        assert "BYBIT_API_KEY" in missing


# ── Tests SecretsManager — Report ────────────────────────────────────────────

class TestSecretsManagerReport:
    """Verifica el reporte de estado (sin valores reales)."""

    def setup_method(self) -> None:
        reset_secrets_manager()

    @pytest.fixture(autouse=True)
    def _cleanup(self) -> Generator:
        yield
        reset_secrets_manager()

    def test_report_contains_secrets_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BYBIT_API_KEY", "real-key")
        monkeypatch.setenv("BYBIT_API_SECRET", "real-secret")
        sm = SecretsManager(config=SecretsConfig(dotenv_path=""))
        report = sm.report()
        assert "secrets" in report
        assert report["secrets"]["BYBIT_API_KEY"]["found"] is True
        assert report["secrets"]["BYBIT_API_SECRET"]["found"] is True
        # Los valores deben estar maskados
        assert "..." in report["secrets"]["BYBIT_API_KEY"]["masked"]

    def test_report_shows_missing_secrets(self) -> None:
        sm = SecretsManager(config=SecretsConfig(
            required_keys=["MISSING_KEY"],
            dotenv_path="",
        ))
        report = sm.report()
        assert report["secrets"]["MISSING_KEY"]["found"] is False

    def test_report_has_source_info(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BYBIT_API_KEY", "real-key")
        sm = SecretsManager(config=SecretsConfig(dotenv_path=""))
        report = sm.report()
        assert report["secrets"]["BYBIT_API_KEY"]["source"] == "env"


# ── Tests SecretsManager — Singleton ─────────────────────────────────────────

class TestSecretsManagerSingleton:
    """Verifica el singleton."""

    def teardown_method(self) -> None:
        reset_secrets_manager()

    def test_get_secrets_manager_returns_singleton(self) -> None:
        sm1 = get_secrets_manager()
        sm2 = get_secrets_manager()
        assert sm1 is sm2

    def test_reset_creates_new_instance(self) -> None:
        sm1 = get_secrets_manager()
        reset_secrets_manager()
        sm2 = get_secrets_manager()
        assert sm1 is not sm2


# ── Tests VaultClient ────────────────────────────────────────────────────────

class TestVaultClient:
    """Verifica el cliente Vault (mockeando HTTP)."""

    @pytest.fixture
    def vault_client(self) -> VaultClient:
        return VaultClient(
            url="http://vault:8200",
            token="test-token",
            mount="secret",
            path="data/trading-bot",
            ttl=300,
        )

    def test_init(self, vault_client: VaultClient) -> None:
        assert vault_client._url == "http://vault:8200"
        assert vault_client._token == "test-token"
        assert vault_client._mount == "secret"
        assert vault_client._path == "data/trading-bot"

    def test_cache_hit(self, vault_client: VaultClient) -> None:
        vault_client._set_cache("MY_KEY", "my-value")
        cached, value = vault_client._is_cached("MY_KEY")
        assert cached is True
        assert value == "my-value"

    def test_cache_miss(self, vault_client: VaultClient) -> None:
        cached, _ = vault_client._is_cached("UNKNOWN")
        assert cached is False

    def test_cache_expiry(self, vault_client: VaultClient) -> None:
        """Cache debe expirar después del TTL."""
        vault_client._ttl = -1  # TTL negativo = expira inmediatamente
        vault_client._set_cache("EXPIRING", "value")
        cached, _ = vault_client._is_cached("EXPIRING")
        assert cached is False

    def test_get_returns_none_when_vault_unavailable(self, vault_client: VaultClient) -> None:
        with patch.object(vault_client, "_fetch_secrets",
                          side_effect=VaultUnavailableError("unavailable")):
            assert vault_client.get("ANY_KEY") is None

    def test_get_finds_key_case_insensitive(self, vault_client: VaultClient) -> None:
        with patch.object(vault_client, "_fetch_secrets",
                          return_value={"data": {"my_key": "value123"}}):
            assert vault_client.get("MY_KEY") == "value123"
            assert vault_client.get("my_key") == "value123"


# ── Tests EncryptedFileStore ─────────────────────────────────────────────────

class TestEncryptedFileStore:
    """Verifica el cifrado/descifrado AES-256-GCM."""

    @pytest.fixture
    def enc_store(self, tmp_path: Path) -> EncryptedFileStore:
        return EncryptedFileStore(file_path=str(tmp_path / "secrets.enc"))

    def test_not_exists(self, tmp_path: Path) -> None:
        store = EncryptedFileStore(file_path=str(tmp_path / "nonexistent.enc"))
        assert store.exists() is False

    def test_save_and_load_roundtrip(self, enc_store: EncryptedFileStore) -> None:
        secrets = {"BYBIT_API_KEY": "my-key", "BYBIT_API_SECRET": "my-secret"}
        enc_store.save(secrets, enc_key="test-key-12345")
        assert enc_store.exists()

        loaded = enc_store.load(enc_key="test-key-12345")
        assert loaded == secrets

    def test_wrong_key_fails(self, enc_store: EncryptedFileStore) -> None:
        enc_store.save({"KEY": "value"}, enc_key="correct-key")
        with pytest.raises(EncryptionError):
            enc_store.load(enc_key="wrong-key")

    def test_rotate_key(self, enc_store: EncryptedFileStore) -> None:
        enc_store.save({"KEY": "my-value"}, enc_key="old-key")
        enc_store.rotate_key(new_key="new-key", old_key="old-key")

        # Vieja clave ya no funciona
        with pytest.raises(EncryptionError):
            enc_store.load(enc_key="old-key")

        # Nueva clave funciona
        loaded = enc_store.load(enc_key="new-key")
        assert loaded == {"KEY": "my-value"}

    def test_empty_secrets(self, enc_store: EncryptedFileStore) -> None:
        enc_store.save({}, enc_key="test-key")
        loaded = enc_store.load(enc_key="test-key")
        assert loaded == {}

    def test_load_from_missing_file(self, tmp_path: Path) -> None:
        store = EncryptedFileStore(file_path=str(tmp_path / "missing.enc"))
        assert store.load(enc_key="any-key") == {}


# ── Tests SecretsManager — Encrypted File ────────────────────────────────────

class TestSecretsManagerEncrypted:
    """Verifica que SecretsManager use el archivo cifrado correctamente."""

    def setup_method(self) -> None:
        reset_secrets_manager()

    @pytest.fixture(autouse=True)
    def _cleanup(self) -> Generator:
        yield
        reset_secrets_manager()

    def test_falls_back_to_encrypted_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sin env vars ni dotenv, debe leer del archivo cifrado."""
        enc_path = tmp_path / "secrets.enc"
        store = EncryptedFileStore(file_path=str(enc_path))
        store.save({"ENC_KEY": "enc-value"}, enc_key="test-cipher-key")

        monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", "test-cipher-key")
        cfg = SecretsConfig(
            dotenv_path="",
            enc_file=str(enc_path),
            enc_key_env="SECRETS_ENCRYPTION_KEY",
        )
        sm = SecretsManager(config=cfg)
        assert sm.get("ENC_KEY") == "enc-value"
        assert sm.get_source("ENC_KEY") == SecretSource.ENCRYPTED


# ── Tests SecretsManager — Edge Cases ────────────────────────────────────────

class TestSecretsManagerEdgeCases:
    """Casos borde del SecretsManager."""

    def setup_method(self) -> None:
        reset_secrets_manager()

    @pytest.fixture(autouse=True)
    def _cleanup(self) -> Generator:
        yield
        reset_secrets_manager()

    def test_get_all_returns_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BYBIT_API_KEY", "key-123")
        monkeypatch.setenv("BYBIT_API_SECRET", "secret-456")
        sm = SecretsManager(config=SecretsConfig(dotenv_path=""))
        all_secrets = sm.get_all()
        assert isinstance(all_secrets, dict)
        # get_all filtra solo lo que coincide con el patrón de secretos
        assert any("BYBIT_API_KEY" in k for k in all_secrets)
        assert any("BYBIT_API_SECRET" in k for k in all_secrets)

    def test_close_cleans_up(self) -> None:
        sm = SecretsManager(config=SecretsConfig(dotenv_path=""))
        sm.initialize()
        assert sm._initialized is True
        sm.close()
        assert sm._initialized is False

    def test_get_source_unknown_for_missing(self) -> None:
        sm = SecretsManager(config=SecretsConfig(dotenv_path=""))
        assert sm.get_source("GHOST_KEY") == SecretSource.UNKNOWN

    def test_initialize_is_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SOME_KEY", "value")
        sm = SecretsManager(config=SecretsConfig(dotenv_path=""))
        sm.initialize()
        sm.initialize()  # segunda llamada no debe fallar
        assert sm._initialized is True


# ── Tests Script encrypt_secrets ─────────────────────────────────────────────

class TestEncryptScript:
    """Verifica la funcionalidad del script encrypt_secrets (parcial)."""

    def test_generate_key_output(self) -> None:
        """generate-key debe producir una salida con SECRETS_ENCRYPTION_KEY=..."""
        from scripts.encrypt_secrets import cmd_generate_key
        with patch("builtins.print") as mock_print:
            cmd_generate_key()
            # Iterar sobre todos los argumentos posicionales de todas las llamadas
            all_args = []
            for call in mock_print.call_args_list:
                all_args.extend(str(a) for a in call[0] if a)
            printed = "".join(all_args)
            assert "SECRETS_ENCRYPTION_KEY=" in printed
