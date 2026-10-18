FROM python:3.12.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    RAY_USAGE_STATS_ENABLED=0 \
    PATH="/opt/tm/.venv/bin:$PATH" \
    TM_DATA_DIR=/var/lib/tm

WORKDIR /opt/tm
# Docker descarga y verifica las capas de la imagen oficial de uv.
# Evita instalar el wheel de uv mediante pip en conexiones interrumpidas.
COPY --from=ghcr.io/astral-sh/uv:0.12.2 /uv /usr/local/bin/uv

# Las dependencias solo cambian cuando cambia su definición o bloqueo.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,id=tm-uv-cache,target=/root/.cache/uv \
    UV_HTTP_TIMEOUT=300 \
    UV_HTTP_CONNECT_TIMEOUT=60 \
    UV_HTTP_RETRIES=8 \
    UV_CONCURRENT_DOWNLOADS=2 \
    uv sync --locked --no-dev --no-install-project

# El código y README invalidan la instalación de nuestro paquete, no la anterior.
COPY README.md ./
COPY src/ ./src/
RUN --mount=type=cache,id=tm-uv-cache,target=/root/.cache/uv \
    UV_HTTP_TIMEOUT=300 \
    UV_HTTP_CONNECT_TIMEOUT=60 \
    UV_HTTP_RETRIES=8 \
    UV_CONCURRENT_DOWNLOADS=2 \
    uv sync --locked --no-dev --no-editable
COPY bots/ ./bots/
COPY deploy/entrypoint.sh /usr/local/bin/tm-entrypoint
RUN chmod 755 /usr/local/bin/tm-entrypoint
ENTRYPOINT ["/usr/local/bin/tm-entrypoint"]
CMD ["coordinator"]

# Metadatos al final: otra revisión Git no invalida herramientas ni dependencias.
ARG TM_REVISION=unknown
LABEL org.opencontainers.image.title="Tournament Management" \
      org.opencontainers.image.revision=$TM_REVISION
