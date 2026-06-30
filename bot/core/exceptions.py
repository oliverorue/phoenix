"""Excepciones personalizadas para el bot de trading."""

class BotException(Exception):
    """Base para todas las excepciones del bot."""

class APIException(BotException):
    """Error de comunicación con el exchange (timeout, rate limit, etc.)."""

class RateLimitException(APIException):
    """Límite de tasa excedido en la API."""

class CircuitBreakerOpen(APIException):
    """Circuit breaker abierto por demasiados fallos consecutivos."""

class StrategyException(BotException):
    """Error en la lógica de evaluación de señales."""

class RiskException(BotException):
    """Violación de límites de riesgo (drawdown, stops diarios, etc.)."""

class PositionException(BotException):
    """Error en la gestión de una posición abierta."""

class ExecutionException(BotException):
    """Error al ejecutar una orden en el exchange."""

class DataFeedException(BotException):
    """Error al obtener datos de mercado (velas, balance, etc.)."""

class StateRecoveryException(BotException):
    """Error al recuperar el estado tras un reinicio."""