"""
OpenTelemetry Tracing — instrumentación distribuida para el bot.

Proporciona:
- TracerProvider configurable (OTLP HTTP o Console)
- Decorador ``@trace`` para auto-instrumentar funciones
- Context manager ``use_span`` para bloques específicos
- Integración con loguru (trace_id en logs)
- Atributos semánticos estándar para operaciones de trading
"""

from __future__ import annotations

import os
import functools
from typing import Any, Callable, Optional, TypeVar

from loguru import logger

# ── Otel imports ─────────────────────────────────────────────
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION

# ── Atributos semánticos personalizados ───────────────────────
ATTR_SYMBOL = "trading.symbol"
ATTR_SIDE = "trading.side"
ATTR_ENTRY = "trading.entry_price"
ATTR_EXIT = "trading.exit_price"
ATTR_PNL_USDT = "trading.pnl_usdt"
ATTR_PNL_PCT = "trading.pnl_pct"
ATTR_REASON = "trading.reason"
ATTR_SCORE = "trading.score"
ATTR_SL = "trading.stop_loss"
ATTR_TP1 = "trading.tp1"
ATTR_TP2 = "trading.tp2"
ATTR_QTY = "trading.quantity"
ATTR_LEVERAGE = "trading.leverage"
ATTR_BALANCE = "trading.balance"
ATTR_EXPOSURE = "trading.exposure"
ATTR_FASE = "trading.fase"
ATTR_INTERVAL = "trading.interval"
ATTR_LATENCY_MS = "trading.latency_ms"
ATTR_ERROR_TYPE = "trading.error_type"
ATTR_WS_TOPIC = "trading.ws_topic"

# ── Estado global (singleton) ────────────────────────────────
_tracer: Optional[otel_trace.Tracer] = None
_provider: Optional[SDKTracerProvider] = None
# Opt-in: solo se activa si TradingBot.init_tracing() lo habilita.
_enabled: bool = False


# ── Inicialización ────────────────────────────────────────────


def init_tracing(
    service_name: str = "trading-bot",
    service_version: str = "4.0.0",
    exporter: str = "console",
    otlp_endpoint: Optional[str] = None,
    sampling_rate: float = 1.0,
) -> otel_trace.Tracer:
    """Inicializa el TracerProvider global.

    Args:
        service_name: Nombre del servicio para identificar traces.
        service_version: Versión del servicio.
        exporter: ``"console"`` (default, stdout) o ``"otlp"`` (HTTP).
        otlp_endpoint: URL del collector OTLP (ej: ``http://localhost:4318/v1/traces``).
        sampling_rate: Ratio de muestreo 0.0–1.0 (1.0 = todas las traces).

    Returns:
        Tracer singleton para crear spans.

    Environment overrides (convención OTEL estándar):
        ``OTEL_SERVICE_NAME``, ``OTEL_EXPORTER_OTLP_ENDPOINT``,
        ``OTEL_TRACES_SAMPLER``, ``OTEL_TRACES_SAMPLER_ARG``.
    """
    global _tracer, _provider, _enabled

    # Permitir override vía environment (estándar OTEL)
    svc = os.environ.get("OTEL_SERVICE_NAME", service_name)
    otel_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", otlp_endpoint)

    resource = Resource.create({
        SERVICE_NAME: svc,
        SERVICE_VERSION: os.environ.get("OTEL_SERVICE_VERSION", service_version),
    })

    _provider = SDKTracerProvider(resource=resource)

    # Span processor según exporter configurado
    if otel_endpoint or exporter == "otlp":
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            otlp_exporter = OTLPSpanExporter(endpoint=otel_endpoint)
            _provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
            logger.info(f"🔍 Tracing OTLP → {otel_endpoint or 'default endpoint'}")
        except Exception as exc:
            logger.warning(f"⚠️ Fallo al conectar OTLP exporter ({exc}), usando console")
            _provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    else:
        _provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        logger.info("🔍 Tracing habilitado → console exporter")

    # Setear provider global
    otel_trace.set_tracer_provider(_provider)

    _tracer = otel_trace.get_tracer(svc, service_version)
    _enabled = True
    return _tracer


def get_tracer() -> otel_trace.Tracer:
    """Retorna el tracer global. Inicializa con defaults si no existe."""
    global _tracer
    if _tracer is None:
        _tracer = init_tracing()
    return _tracer


def set_enabled(enabled: bool) -> None:
    """Activa/desactiva tracing globalmente."""
    global _enabled
    _enabled = enabled


def shutdown_tracing() -> None:
    """Force-flush y shutdown del provider (útil en stop del bot)."""
    if _provider is not None:
        try:
            _provider.force_flush()
            _provider.shutdown()
        except Exception:
            pass


# ── Decorador @trace ─────────────────────────────────────────

F = TypeVar("F", bound=Callable[..., Any])


def trace(
    span_name: Optional[str] = None,
    attributes: Optional[dict[str, Any]] = None,
    record_exception: bool = True,
) -> Callable[[F], F]:
    """Decorador que envuelve una función en un span de OpenTelemetry.

    El nombre del span por defecto es ``{ClassName}.{method_name}``
    o ``{module}.{function_name}``.

    Los argumentos nombrados que coincidan con claves en *attributes* se
    añaden automáticamente como atributos del span.

    Args:
        span_name: Nombre personalizado para el span (default: inferido).
        attributes: Diccionario de atributos estáticos a añadir al span.
        record_exception: Si True, captura excepciones como eventos del span.

    Usage::

        @trace(attributes={"trading.symbol": "XRPUSDT"})
        async def run_cycle(self, symbol: str) -> None:
            ...
    """
    def decorator(func: F) -> F:
        is_async = __import__("inspect").iscoroutinefunction(func)

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            if not _enabled:
                return await func(*args, **kwargs)
            tracer = get_tracer()
            name = span_name or _infer_name(func, args)
            with tracer.start_as_current_span(name) as span:
                _set_attributes(span, attributes, kwargs)
                try:
                    result = await func(*args, **kwargs)
                    return result
                except Exception as exc:
                    if record_exception:
                        span.record_exception(exc)
                        span.set_status(otel_trace.Status(otel_trace.StatusCode.ERROR, str(exc)))
                    raise

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            if not _enabled:
                return func(*args, **kwargs)
            tracer = get_tracer()
            name = span_name or _infer_name(func, args)
            with tracer.start_as_current_span(name) as span:
                _set_attributes(span, attributes, kwargs)
                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception as exc:
                    if record_exception:
                        span.record_exception(exc)
                        span.set_status(otel_trace.Status(otel_trace.StatusCode.ERROR, str(exc)))
                    raise

        return async_wrapper if is_async else sync_wrapper  # type: ignore

    return decorator


# ── Context manager use_span ─────────────────────────────────


class use_span:
    """Context manager para crear spans en bloques específicos.

    Usage::

        with use_span("process_order", attributes={"trading.symbol": "XRPUSDT"}):
            ...
    """

    def __init__(
        self,
        name: str,
        attributes: Optional[dict[str, Any]] = None,
        record_exception: bool = True,
    ) -> None:
        self.name = name
        self.attrs = attributes or {}
        self.record_exception = record_exception

    def __enter__(self) -> otel_trace.Span:
        if not _enabled:
            self._noop = True
            return otel_trace.get_current_span()  # no-op span
        self._noop = False
        tracer = get_tracer()
        self._span = tracer.start_span(self.name)
        if self.attrs:
            self._span.set_attributes(self.attrs)
        return self._span

    def __exit__(
        self,
        exc_type: Optional[type],
        exc_val: Optional[BaseException],
        exc_tb: Any,
    ) -> None:
        if self._noop:
            return
        if exc_val and self.record_exception:
            self._span.record_exception(exc_val)
            self._span.set_status(otel_trace.Status(otel_trace.StatusCode.ERROR, str(exc_val)))
        self._span.end()


# ── Helpers ──────────────────────────────────────────────────


def _infer_name(func: Callable[..., Any], args: tuple[Any, ...]) -> str:
    """Infringe nombre de span desde la función y sus argumentos."""
    qual = getattr(func, "__qualname__", func.__name__)
    return qual


def _set_attributes(
    span: otel_trace.Span,
    static_attrs: Optional[dict[str, Any]],
    call_kwargs: dict[str, Any],
) -> None:
    """Mezcla atributos estáticos con kwargs relevantes."""
    if static_attrs:
        span.set_attributes(static_attrs)
    # Añadir kwargs que parezcan atributos de trading
    for key in ("symbol", "side", "entry", "exit_price", "pnl_usdt", "pnl_pct",
                 "reason", "score", "sl", "tp1", "tp2", "qty", "leverage",
                 "interval", "balance", "fase", "latency_ms", "error_type"):
        if key in call_kwargs:
            span.set_attribute(f"trading.{key}", call_kwargs[key])


# ── Utilidad: trace_id en logs ───────────────────────────────


def current_trace_id() -> Optional[str]:
    """Retorna el trace_id del span actual (para inyectar en logs)."""
    span = otel_trace.get_current_span()
    if not span:
        return None
    ctx = span.get_span_context()
    if ctx.trace_id == 0:
        return None
    return format(ctx.trace_id, "032x")


def trace_id_filter(record: dict[str, Any]) -> None:
    """Filtro loguru que añade trace_id al dict del record.

    Uso::

        logger.configure(patcher=trace_id_filter)
    """
    tid = current_trace_id()
    record["extra"]["trace_id"] = tid or ""
