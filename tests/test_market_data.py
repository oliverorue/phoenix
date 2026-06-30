"""Tests para el módulo MarketDataProvider (FASE 5.2)."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from bot.core.market_data import MarketDataProvider


class TestMarketDataProviderInit:
    """Verifica que el constructor funcione con DI y sin ella."""

    def test_init_with_di(self):
        """Inyecta un BinanceFuturesClient y circuit_breaker explícitamente."""
        session = MagicMock()
        cb = MagicMock()
        provider = MarketDataProvider(http_session=session, circuit_breaker=cb)
        assert provider._client is session
        assert provider.circuit_breaker is cb

    def test_init_without_di(self):
        """Sin DI, debe crear BinanceFuturesClient dentro del constructor."""
        with patch("bot.core.market_data.BinanceFuturesClient") as mock_client:
            mock_client.return_value = MagicMock()
            provider = MarketDataProvider()
            mock_client.assert_called_once()
            assert provider._client is not None
            assert provider.circuit_breaker is not None


class TestGetFundingRate:
    """Prueba get_funding_rate con client mockeado."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        # BinanceFuturesClient.get_funding_rate es un async method
        client.get_funding_rate = AsyncMock()
        return client

    @pytest.fixture
    def provider(self, mock_client):
        cb = MagicMock()
        cb.can_execute.return_value = True
        return MarketDataProvider(http_session=mock_client, circuit_breaker=cb)

    @pytest.mark.asyncio
    async def test_get_funding_rate_returns_data(self, provider, mock_client):
        """Responde con funding rate válido."""
        mock_client.get_funding_rate.return_value = {
            "symbol": "XRPUSDT",
            "funding_rate": 0.0001,
            "funding_time": 1714838400000,
            "mark_price": 0.6185,
        }
        result = await provider.get_funding_rate("XRPUSDT")
        assert result is not None
        assert result["symbol"] == "XRPUSDT"
        assert result["funding_rate"] == 0.0001
        assert result["funding_time"] is not None

    @pytest.mark.asyncio
    async def test_get_funding_rate_empty_result(self, provider, mock_client):
        """None de la API → None."""
        mock_client.get_funding_rate.return_value = None
        result = await provider.get_funding_rate("XRPUSDT")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_funding_rate_api_error(self, provider, mock_client):
        """Error de API → None (no crashing)."""
        from bot.core.exchange_client import MarketDataException as ClientMarketDataException
        mock_client.get_funding_rate.side_effect = ClientMarketDataException("API timeout")
        result = await provider.get_funding_rate("XRPUSDT")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_funding_rate_cache_hit(self, provider, mock_client):
        """Segunda llamada con mismo símbolo usa cache (no llama a la API)."""
        mock_client.get_funding_rate.return_value = {
            "symbol": "XRPUSDT",
            "funding_rate": 0.0001,
            "funding_time": 1714838400000,
            "mark_price": 0.6185,
        }
        r1 = await provider.get_funding_rate("XRPUSDT")
        assert r1 is not None

        # Reset mock para verificar que no se llama de nuevo
        mock_client.get_funding_rate.reset_mock()
        r2 = await provider.get_funding_rate("XRPUSDT")
        assert r2 is not None
        assert r2["funding_rate"] == 0.0001
        mock_client.get_funding_rate.assert_not_called()


class TestGetOpenInterest:
    """Prueba get_open_interest con client mockeado."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        client.get_open_interest = AsyncMock()
        return client

    @pytest.fixture
    def provider(self, mock_client):
        cb = MagicMock()
        cb.can_execute.return_value = True
        return MarketDataProvider(http_session=mock_client, circuit_breaker=cb)

    @pytest.mark.asyncio
    async def test_get_open_interest_returns_data(self, provider, mock_client):
        """Responde con OI válido."""
        mock_client.get_open_interest.return_value = {
            "symbol": "XRPUSDT",
            "open_interest": 1500000.0,
            "time": 1714838400000,
        }
        result = await provider.get_open_interest("XRPUSDT")
        assert result is not None
        assert result["symbol"] == "XRPUSDT"
        assert result["open_interest"] == 1500000.0

    @pytest.mark.asyncio
    async def test_get_open_interest_empty(self, provider, mock_client):
        """None de la API → None."""
        mock_client.get_open_interest.return_value = None
        result = await provider.get_open_interest("XRPUSDT")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_open_interest_cache_hit(self, provider, mock_client):
        """Cache funciona."""
        mock_client.get_open_interest.return_value = {
            "symbol": "XRPUSDT",
            "open_interest": 1000000.0,
            "time": 1714838400000,
        }
        r1 = await provider.get_open_interest("XRPUSDT")
        assert r1 is not None

        mock_client.get_open_interest.reset_mock()
        r2 = await provider.get_open_interest("XRPUSDT")
        assert r2 is not None
        mock_client.get_open_interest.assert_not_called()


class TestGetMarketSignal:
    """Prueba get_market_signal con diferentes escenarios de funding rate."""

    @pytest.fixture
    def mock_client(self):
        c = MagicMock()
        c.get_funding_rate = AsyncMock()
        return c

    @pytest.fixture
    def provider(self, mock_client):
        cb = MagicMock()
        cb.can_execute.return_value = True
        return MarketDataProvider(http_session=mock_client, circuit_breaker=cb)

    async def _seed_funding(self, provider, mock_client, fr_value: float):
        """Helper: seedea funding rate en cache."""
        mock_client.get_funding_rate.return_value = {
            "symbol": "XRPUSDT",
            "funding_rate": fr_value,
            "funding_time": 1714838400000,
            "mark_price": 0.6185,
        }
        await provider.get_funding_rate("XRPUSDT")

    @pytest.mark.asyncio
    async def test_signal_extreme_high_funding(self, provider, mock_client):
        """Funding extremo (>0.01) → bearish (bloquea LONG)."""
        await self._seed_funding(provider, mock_client, 0.02)
        sig = provider.get_market_signal("XRPUSDT")
        assert sig["signal"] == "bearish"
        assert "Funding extremo" in sig["reason"]

    @pytest.mark.asyncio
    async def test_signal_extreme_low_funding(self, provider, mock_client):
        """Funding extremo bajo (< -0.01) → bullish (bloquea SHORT)."""
        await self._seed_funding(provider, mock_client, -0.02)
        sig = provider.get_market_signal("XRPUSDT")
        assert sig["signal"] == "bullish"
        assert "Funding negativo" in sig["reason"]

    @pytest.mark.asyncio
    async def test_signal_moderate_funding_stays_neutral(self, provider, mock_client):
        """Funding moderado (>0.006 pero <0.01) → neutral (no bloquea nada)."""
        await self._seed_funding(provider, mock_client, 0.008)
        sig = provider.get_market_signal("XRPUSDT")
        assert sig["signal"] == "neutral"
        # Reason vacío porque funding moderado no genera acción
        assert sig["reason"] == ""

    @pytest.mark.asyncio
    async def test_signal_moderate_low_funding_stays_neutral(self, provider, mock_client):
        """Funding bajo moderado (< -0.006 pero > -0.01) → neutral."""
        await self._seed_funding(provider, mock_client, -0.008)
        sig = provider.get_market_signal("XRPUSDT")
        assert sig["signal"] == "neutral"
        assert sig["reason"] == ""

    @pytest.mark.asyncio
    async def test_signal_neutral_funding(self, provider, mock_client):
        """Funding cercano a 0 → neutral."""
        await self._seed_funding(provider, mock_client, 0.0001)
        sig = provider.get_market_signal("XRPUSDT")
        assert sig["signal"] == "neutral"
        assert sig["reason"] == ""

    @pytest.mark.asyncio
    async def test_signal_no_cache(self, provider, mock_client):
        """Sin funding rate en cache → neutral."""
        sig = provider.get_market_signal("XRPUSDT")
        assert sig["signal"] == "neutral"

    @pytest.mark.asyncio
    async def test_clear_cache(self, provider, mock_client):
        """clear_cache vacía los caches internos."""
        await self._seed_funding(provider, mock_client, 0.02)
        assert len(provider._funding_cache) == 1
        provider.clear_cache()
        assert len(provider._funding_cache) == 0
