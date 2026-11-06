import pytest
from unittest.mock import MagicMock, AsyncMock
from bot.core.risk_manager import RiskManager

class TestPositionSize:
    @pytest.fixture
    def mock_data_feed(self):
        """Simula DataFeed con balance y info del instrumento."""
        feed = MagicMock()
        feed.get_balance = AsyncMock(return_value=200.0)
        feed.get_instrument_info = AsyncMock(return_value={
            "qtyStep": 1.0,
            "minOrderQty": 1.0,
            "maxOrderQty": 500000.0,
            "minNotionalValue": 5.0,
            "maxNotionalValue": 1000000.0,
        })
        feed.symbol = "XRPUSDT"
        return feed

    @pytest.fixture
    def risk_mgr(self, mock_data_feed):
        mgr = RiskManager(mock_data_feed)
        # Fijar balance para backtesting (simula set_balance)
        mgr.set_balance(200.0)
        return mgr

    @pytest.mark.asyncio
    async def test_basic_position_size(self, risk_mgr):
        """Con SL a 1%, riesgo 2%, balance 200 => qty limitada por margen."""
        qty, _, msg = await risk_mgr.calculate_position_size(entry_price=0.50, sl=0.495)
        assert msg == "OK"
        assert qty > 0
        # qty teórica = (200 * 0.02) / 0.005 = 800, pero margen limita a (200*0.8)*1 / 0.50 = 320
        # Por step de 1.0, qty = 320
        assert qty == pytest.approx(320, abs=1)

    @pytest.mark.asyncio
    async def test_zero_sl_rejected(self, risk_mgr):
        qty, _, msg = await risk_mgr.calculate_position_size(0.50, 0.50)
        assert qty == 0
        assert "SL distance is zero" in msg

    @pytest.mark.asyncio
    async def test_margin_limit_respected(self, risk_mgr):
        """Si el margen excede el 80% del balance, se reduce la cantidad."""
        risk_mgr.data.get_balance.return_value = 20.0
        risk_mgr.set_balance(20.0)
        qty, ok, _ = await risk_mgr.calculate_position_size(0.50, 0.495)
        # Notional máximo = 20 * 0.8 * leverage 1 = 16
        # qty máxima = 16 / 0.50 = 32, ajustado por step 1.0 => 32
        assert ok
        assert qty * 0.50 <= 16.0


class TestDrawdown:
    """Tests para el sistema de drawdown diario y por sesión."""

    @pytest.fixture
    def mock_data_feed(self):
        feed = MagicMock()
        feed.get_balance = AsyncMock(return_value=200.0)
        feed.get_instrument_info = AsyncMock(return_value={
            "qtyStep": 1.0, "minOrderQty": 1.0, "maxOrderQty": 500000.0,
            "minNotionalValue": 5.0, "maxNotionalValue": 1000000.0,
        })
        feed.symbol = "XRPUSDT"
        return feed

    @pytest.fixture
    def risk_mgr(self, mock_data_feed):
        """Crea RiskManager SIN set_balance para que get_balance use el mock."""
        mgr = RiskManager(mock_data_feed)
        return mgr

    @pytest.mark.asyncio
    async def test_drawdown_zero_when_no_change(self, risk_mgr):
        """Si el balance no ha cambiado, el drawdown debe ser 0."""
        dd = await risk_mgr.get_current_drawdown_pct()
        assert dd == 0.0

    @pytest.mark.asyncio
    async def test_drawdown_tracks_peak(self, risk_mgr):
        """El drawdown se calcula desde el peak diario."""
        await risk_mgr.update_balance_for_drawdown(200.0)
        await risk_mgr.update_balance_for_drawdown(180.0)  # Baja a 180
        # Sincronizar mock para que get_balance retorne el valor actual
        risk_mgr.data.get_balance.return_value = 180.0
        dd = await risk_mgr.get_current_drawdown_pct()
        assert dd == pytest.approx(10.0, abs=0.01)  # (200-180)/200 * 100 = 10%

    @pytest.mark.asyncio
    async def test_drawdown_updates_peak_on_higher_balance(self, risk_mgr):
        """Si el balance sube, el peak se actualiza."""
        await risk_mgr.update_balance_for_drawdown(200.0)
        await risk_mgr.update_balance_for_drawdown(220.0)  # Nuevo peak
        await risk_mgr.update_balance_for_drawdown(200.0)  # Baja
        dd = await risk_mgr.get_current_drawdown_pct()
        assert dd == pytest.approx(9.09, abs=0.1)  # (220-200)/220 * 100 ≈ 9.09%

    @pytest.mark.asyncio
    async def test_validate_drawdown_raises_when_exceeded(self, risk_mgr):
        """validate_drawdown debe lanzar RiskException si se supera max_dd_daily."""
        from bot.core.exceptions import RiskException
        # Simular que el balance cayó mucho
        risk_mgr._peak_balance_daily = 200.0
        risk_mgr.data.get_balance.return_value = 180.0  # DD = 10%
        # max_dd_daily = 0.06 → 6%, DD actual 10% > 6%
        # También sincronizar el peak de sesión para que no dispare primero
        risk_mgr._peak_balance_session = 200.0
        with pytest.raises(RiskException, match="Drawdown diario"):
            await risk_mgr.validate_drawdown()

    @pytest.mark.asyncio
    async def test_validate_drawdown_ok_when_under_limit(self, risk_mgr):
        """validate_drawdown no debe lanzar si el DD está dentro del límite."""
        from bot.core.exceptions import RiskException
        risk_mgr._peak_balance_daily = 200.0
        risk_mgr.data.get_balance.return_value = 195.0  # DD = 2.5%
        # max_dd_daily = 6%, DD actual 2.5% < 6%
        try:
            await risk_mgr.validate_drawdown()
        except RiskException:
            pytest.fail("No debería lanzar excepción con DD dentro del límite")

    @pytest.mark.asyncio
    async def test_session_drawdown_more_strict_than_daily(self, risk_mgr):
        """El drawdown por sesión tiene un límite más estricto (80% del diario)."""
        from bot.core.exceptions import RiskException
        # Forzar el límite de fase 0 (6%) sin depender de la fase por balance.
        risk_mgr._override_cfg = {"max_dd_daily": 0.06}
        # Simular DD diario de 5% (dentro del 6% diario) pero sesión de 5% (excede 4.8%)
        risk_mgr._peak_balance_daily = 200.0
        risk_mgr._peak_balance_session = 200.0
        risk_mgr.data.get_balance.return_value = 190.0  # DD = 5%

        # get_current_drawdown_pct() actualiza el peak si el balance es mayor.
        # Al balance actual (190) ser menor que el peak (200), no debería cambiarlo.
        # DD diario = (200-190)/200*100 = 5% (OK, dentro del 6%)
        # DD sesión = (200-190)/200*100 = 5% (excede 4.8% → debe lanzar)
        with pytest.raises(RiskException, match="Drawdown de sesión"):
            await risk_mgr.validate_drawdown()

    @pytest.mark.asyncio
    async def test_session_drawdown_resets_on_new_day(self, risk_mgr):
        """El peak de sesión se resetea junto con el diario."""
        from datetime import datetime
        import pytz
        from config.loader import TIMEZONE_NAME
        tz = pytz.timezone(TIMEZONE_NAME)
        # Inicializar fecha_dia con HOY para que reset_daily_if_needed no reseteé
        risk_mgr.fecha_dia = datetime.now(tz).date()
        risk_mgr._peak_balance_session = 200.0
        await risk_mgr.reset_daily_if_needed()
        # Como es el mismo día (fecha_dia == hoy), NO debería resetear
        assert risk_mgr._peak_balance_session == 200.0