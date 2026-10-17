FROM python:3.12.13-slim-bookworm

ARG TM_REVISION=unknown
LABEL org.opencontainers.image.title="Tournament Management" \
      org.opencontainers.image.revision=$TM_REVISION

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    RAY_USAGE_STATS_ENABLED=0 \
    PATH="/opt/tm/.venv/bin:$PATH" \
    TM_DATA_DIR=/var/lib/tm

WORKDIR /opt/tm
RUN pip install --no-cache-dir uv==0.12.2
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
COPY bots/ ./bots/
# La caché sobrevive a fallos de este RUN; no forma parte de la imagen final.
# Mantener los pasos anteriores intactos permite reutilizar Python y uv.
RUN --mount=type=cache,id=tm-uv-cache,target=/root/.cache/uv \
    UV_HTTP_TIMEOUT=300 \
    UV_HTTP_CONNECT_TIMEOUT=60 \
    UV_HTTP_RETRIES=8 \
    UV_CONCURRENT_DOWNLOADS=2 \
    uv sync --locked --no-dev --no-editable
COPY deploy/entrypoint.sh /usr/local/bin/tm-entrypoint
RUN chmod 755 /usr/local/bin/tm-entrypoint
ENTRYPOINT ["/usr/local/bin/tm-entrypoint"]
CMD ["coordinator"]
