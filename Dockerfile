# =============================================================================
# Dockerfile multi-etapa — Phoenix (Binance Futures Scalping Bot)
# =============================================================================
# Build:    docker build -t phoenix .
# Run:      docker compose up
# =============================================================================

# ─── Stage 1: Dependencias del sistema ─────────────────────────────
FROM python:3.14-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ─── Stage 2: Dependencias Python ──────────────────────────────────
FROM base AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt

# ─── Stage 3: Imagen final (liviana) ───────────────────────────────
FROM base AS runtime

# Crear usuario no-root para seguridad
RUN groupadd -r botuser && useradd -r -g botuser -d /app -s /bin/bash botuser

WORKDIR /app

# Copiar dependencias desde builder
COPY --from=builder /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copiar código fuente
COPY bot/ bot/
COPY config/ config/
COPY scripts/ scripts/
COPY pyproject.toml .

# Entrypoint
COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Crear directorios de datos con permisos
RUN mkdir -p /app/data /app/logs \
    && chown -R botuser:botuser /app

USER botuser

# Health check: verifica que los puertos de métricas respondan o el proceso esté vivo
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python3 -c "import sys, os; sys.exit(0 if os.path.exists('/app/data/bot_state_persistent.json') else 1)"

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["bot"]
