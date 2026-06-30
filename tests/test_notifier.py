"""
Tests para Notifier: envío de mensajes a Telegram y Discord.
Usa mocks para evitar llamadas reales a las APIs.
"""

import pytest
from unittest.mock import patch
from bot.core.notifier import Notifier


# Parcheamos los valores de configuración ANTES de importar Notifier
# para evitar que cargue las credenciales reales de secrets.env
@pytest.fixture(autouse=True)
def _mock_config(monkeypatch):
    monkeypatch.setattr("bot.core.notifier.TELEGRAM_TOKEN", "")
    monkeypatch.setattr("bot.core.notifier.TELEGRAM_CHAT_ID", "")
    monkeypatch.setattr("bot.core.notifier.DISCORD_WEBHOOK", "")


class TestNotifier:
    @pytest.fixture
    def notifier(self):
        """Notifier sin dependencias reales."""
        return Notifier()

    def test_initial_state(self, notifier):
        """Estado inicial del notifier."""
        assert hasattr(notifier, "telegram_token")
        assert hasattr(notifier, "telegram_chat")
        assert hasattr(notifier, "discord_webhook")

    @patch("requests.post")
    def test_send_telegram(self, mock_post, notifier):
        """Envía un mensaje a Telegram correctamente."""
        mock_post.return_value.status_code = 200
        notifier.telegram_token = "fake_token_123"
        notifier.telegram_chat = "123456"
        notifier.enabled = True

        notifier.send("Test Title", "Test message", severity="INFO")

        # Verificar que se llamó a Telegram
        telegram_calls = [c for c in mock_post.call_args_list
                          if "api.telegram.org" in str(c)]
        assert len(telegram_calls) > 0

    @patch("requests.post")
    def test_send_discord(self, mock_post, notifier):
        """Envía un mensaje a Discord correctamente."""
        mock_post.return_value.status_code = 204
        notifier.discord_webhook = "https://discord.com/api/webhooks/fake"
        notifier.enabled = True

        notifier.send("Test Title", "Test message", severity="WARNING")

        discord_calls = [c for c in mock_post.call_args_list
                         if "discord.com" in str(c)]
        assert len(discord_calls) > 0

    @patch("requests.post")
    def test_send_telegram_http_error(self, mock_post, notifier):
        """Error HTTP de Telegram no propaga excepción."""
        mock_post.return_value.status_code = 400
        mock_post.return_value.text = "Bad Request"
        notifier.telegram_token = "fake_token_123"
        notifier.telegram_chat = "123456"
        notifier.enabled = True

        # No debe lanzar excepción
        notifier.send("Test", "Message", severity="INFO")

    @patch("requests.post")
    def test_send_discord_http_error(self, mock_post, notifier):
        """Error HTTP de Discord no propaga excepción."""
        mock_post.return_value.status_code = 404
        mock_post.return_value.text = "Not Found"
        notifier.discord_webhook = "https://discord.com/api/webhooks/fake"
        notifier.enabled = True

        notifier.send("Test", "Message", severity="ERROR")

    @patch("requests.post")
    def test_send_connection_error(self, mock_post, notifier):
        """Error de conexión no propaga excepción."""
        mock_post.side_effect = Exception("Connection refused")
        notifier.telegram_token = "fake_token_123"
        notifier.telegram_chat = "123456"
        notifier.enabled = True

        # No debe lanzar excepción al tener error de conexión
        notifier.send("Test", "Message", severity="INFO")

    @patch("requests.post")
    def test_send_only_configured_channels(self, mock_post, notifier):
        """Solo envía a los canales que tienen configuración."""
        # Sin configuración, notifier.enabled es False
        notifier.send("Test", "Message", severity="INFO")
        # enabled=False → no llama a nada
        mock_post.assert_not_called()

    @patch("requests.post")
    def test_send_telegram_only(self, mock_post, notifier):
        """Si solo Telegram está configurado, no envía a Discord."""
        mock_post.return_value.status_code = 200
        notifier.telegram_token = "fake_token_123"
        notifier.telegram_chat = "123456"
        notifier.discord_webhook = ""
        notifier.enabled = True

        notifier.send("Test", "Message", severity="INFO")

        discord_calls = [c for c in mock_post.call_args_list
                         if "discord.com" in str(c)]
        assert len(discord_calls) == 0
        telegram_calls = [c for c in mock_post.call_args_list
                          if "api.telegram.org" in str(c)]
        assert len(telegram_calls) > 0


class TestNotifierAlertTypes:
    """Verifica los métodos de alto nivel (trade_open, error_alert, etc.)."""

    @pytest.fixture
    def notifier(self):
        n = Notifier()
        n.telegram_token = "fake"
        n.telegram_chat = "123"
        n.discord_webhook = ""
        n.enabled = True
        return n

    @patch.object(Notifier, "send")
    def test_trade_open(self, mock_send, notifier):
        """trade_open llama a send con los parámetros correctos."""
        notifier.trade_open(
            symbol="XRPUSDT", side="LONG", entry=0.50,
            sl=0.495, tp1=0.51, tp2=0.52, score=80
        )
        mock_send.assert_called_once()
        args, _ = mock_send.call_args
        # args[0] = title, args[1] = message, args[2] = severity
        assert "LONG" in args[0] or "LONG" in args[1]

    @patch.object(Notifier, "send")
    def test_trade_close(self, mock_send, notifier):
        """trade_close llama a send con los parámetros correctos."""
        notifier.trade_close(
            symbol="XRPUSDT", side="LONG", entry=0.50,
            exit_price=0.52, pnl_usdt=2.0, pnl_pct=4.0,
            reason="tp1"
        )
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        assert "tp1" in str(args) or "tp1" in str(kwargs)

    @patch.object(Notifier, "send")
    def test_error_alert(self, mock_send, notifier):
        """error_alert usa severidad CRITICAL y alert_type='error'."""
        notifier.error_alert("API Error: Bybit no responde")
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        # args = (title, message, severity); kwargs = {alert_type}
        assert len(args) >= 3
        assert args[2] == "CRITICAL"
        assert kwargs.get("alert_type") == "error"

    @patch.object(Notifier, "send")
    def test_stop_limit(self, mock_send, notifier):
        """stop_limit informa límite de stops alcanzado."""
        notifier.stop_limit(stops_today=5, max_stops=10)
        mock_send.assert_called_once()
        _, kwargs = mock_send.call_args
        assert kwargs.get("alert_type") == "stop_limit"


class TestNotifierCooldown:
    """Verifica el sistema de cooldown para evitar spam."""

    @pytest.fixture
    def notifier(self):
        n = Notifier()
        n.telegram_token = "fake"
        n.telegram_chat = "123"
        n.discord_webhook = ""
        n.enabled = True
        return n

    @patch("time.time")
    @patch.object(Notifier, "_send_telegram")
    def test_cooldown_suppresses_duplicates(self, mock_telegram, mock_time, notifier):
        """Mensajes duplicados dentro del cooldown se suprimen."""
        mock_time.return_value = 1000.0

        notifier.send("Test", "Duplicate message", severity="INFO", alert_type="generic")
        assert mock_telegram.call_count == 1

        # Segundo envío dentro del cooldown (mismo alert_type)
        mock_time.return_value = 1005.0  # 5s después, cooldown genérico es 10s
        notifier.send("Test", "Duplicate message", severity="INFO", alert_type="generic")
        assert mock_telegram.call_count == 1  # No se incrementa

    @patch("time.time")
    @patch.object(Notifier, "_send_telegram")
    def test_cooldown_expires(self, mock_telegram, mock_time, notifier):
        """Pasado el cooldown, se permite reenviar."""
        mock_time.return_value = 1000.0

        notifier.send("Test", "Message", severity="INFO", alert_type="generic")
        assert mock_telegram.call_count == 1

        # Avanzar el tiempo más allá del cooldown (genérico = 10s)
        mock_time.return_value = 1015.0
        notifier.send("Test", "Message", severity="INFO", alert_type="generic")
        assert mock_telegram.call_count == 2  # Ahora sí se envía
