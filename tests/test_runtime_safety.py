"""Tests de seguridad de arranque (Fase 0)."""

import pytest

import config.loader as loader


@pytest.fixture(autouse=True)
def _no_testnet_conflict(monkeypatch):
    """Aísla los tests del entorno local del operador."""
    monkeypatch.setattr(loader, "TESTNET_CONFLICT", False)
    monkeypatch.setattr(loader, "LIVE_TRADING_CONFIRMED", False)


class TestResolveTestnet:
    def test_settings_is_default_source(self):
        assert loader.resolve_testnet(True, None) == (True, "settings")
        assert loader.resolve_testnet(False, "") == (False, "settings")

    def test_env_is_explicit_override(self):
        assert loader.resolve_testnet(True, "false") == (False, "env")
        assert loader.resolve_testnet(False, "true") == (True, "env")


class TestValidateRuntimeSafety:
    def test_paper_mode_allows_missing_keys(self):
        errors = loader.validate_runtime_safety(
            paper_mode=True, testnet=False, api_key=None, api_secret=None
        )
        assert errors == []

    def test_non_paper_requires_api_keys(self):
        errors = loader.validate_runtime_safety(
            paper_mode=False, testnet=True, api_key="", api_secret=""
        )
        assert any("BINANCE_API_KEY" in e for e in errors)
        assert any("BINANCE_API_SECRET" in e for e in errors)

    def test_placeholder_keys_are_rejected(self):
        errors = loader.validate_runtime_safety(
            paper_mode=False,
            testnet=True,
            api_key="TU_API_KEY_AQUI",
            api_secret="TU_API_SECRET_AQUI",
        )
        assert any("BINANCE_API_KEY" in e for e in errors)
        assert any("BINANCE_API_SECRET" in e for e in errors)

    def test_mainnet_requires_explicit_confirmation(self):
        errors = loader.validate_runtime_safety(
            paper_mode=False,
            testnet=False,
            api_key="real-key",
            api_secret="real-secret",
        )
        assert any("LIVE_TRADING_CONFIRMED" in e for e in errors)

    def test_mainnet_with_confirmation_is_allowed(self, monkeypatch):
        monkeypatch.setattr(loader, "LIVE_TRADING_CONFIRMED", True)
        errors = loader.validate_runtime_safety(
            paper_mode=False,
            testnet=False,
            api_key="real-key",
            api_secret="real-secret",
        )
        assert errors == []

    def test_testnet_real_orders_do_not_require_mainnet_confirmation(self):
        errors = loader.validate_runtime_safety(
            paper_mode=False,
            testnet=True,
            api_key="real-key",
            api_secret="real-secret",
        )
        assert errors == []

    def test_testnet_conflict_is_reported(self, monkeypatch):
        monkeypatch.setattr(loader, "TESTNET_CONFLICT", True)
        errors = loader.validate_runtime_safety(paper_mode=True)
        assert any("Conflicto de entorno" in e for e in errors)


class TestAssertRuntimeSafety:
    def test_raises_with_clear_message(self):
        with pytest.raises(loader.RuntimeSafetyError, match="BINANCE_API_KEY"):
            loader.assert_runtime_safety(
                paper_mode=False, testnet=True, api_key="", api_secret=""
            )

    def test_does_nothing_when_safe(self):
        loader.assert_runtime_safety(
            paper_mode=True, testnet=True, api_key=None, api_secret=None
        )


class TestTradingBotStartupGuard:
    @pytest.mark.asyncio
    async def test_trading_bot_refuses_mainnet_without_confirmation(
        self, monkeypatch
    ):
        import bot.main as main

        monkeypatch.setattr(loader, "TESTNET", False)
        monkeypatch.setattr(loader, "LIVE_TRADING_CONFIRMED", False)
        monkeypatch.setattr(main, "PAPER_TRADING_ENABLED", False)

        bot = main.TradingBot.__new__(main.TradingBot)
        with pytest.raises(loader.RuntimeSafetyError, match="LIVE_TRADING_CONFIRMED"):
            await bot._async_init()
