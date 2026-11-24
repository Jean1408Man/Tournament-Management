# Dos imágenes (L2): `worker` para el head y los workers, sin PostgreSQL, y
# `coordinator`, que lleva su base local. Las construye build-image.sh.
FROM python:3.12.13-slim-trixie AS base

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

FROM base AS worker
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
COPY deploy/docker/entrypoint.sh /usr/local/bin/tm-entrypoint
RUN chmod 755 /usr/local/bin/tm-entrypoint
ENTRYPOINT ["/usr/local/bin/tm-entrypoint"]
CMD ["ray-worker"]

# Metadatos al final: otra revisión Git no invalida herramientas ni dependencias.
ARG TM_REVISION=unknown
LABEL org.opencontainers.image.title="Tournament Management" \
      org.opencontainers.image.revision=$TM_REVISION

FROM base AS coordinator
# PostgreSQL de Debian antes que el código: un cambio de código no lo descarga
# otra vez. Sin la base de ejemplo que el paquete crea al instalarse.
ARG PG_MAJOR=17
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-common \
    && sed -ri 's/#(create_main_cluster) .*$/\1 = false/' /etc/postgresql-common/createcluster.conf \
    && grep -q '^create_main_cluster = false' /etc/postgresql-common/createcluster.conf \
    && apt-get install -y --no-install-recommends "postgresql-$PG_MAJOR" \
    && rm -rf /var/lib/apt/lists/* \
    && install -d -o postgres -g postgres -m 755 /run/postgresql \
    && install -d -o postgres -g postgres -m 700 /var/lib/postgresql/data
# La base es local al nodo: solo socket Unix, sin puerto (L4). La URL va en la
# imagen para que también la usen los docker exec de las herramientas.
ENV PG_MAJOR=$PG_MAJOR \
    PATH="/usr/lib/postgresql/$PG_MAJOR/bin:$PATH" \
    TM_PGDATA=/var/lib/postgresql/data \
    TM_DATABASE_URL="postgresql:///tournament_management?host=/run/postgresql&user=tm"
# Mismo paquete ya instalado en `worker`: las dos imágenes llevan el mismo código.
COPY --from=worker /opt/tm /opt/tm
COPY --from=worker /usr/local/bin/tm-entrypoint /usr/local/bin/tm-entrypoint
ENTRYPOINT ["/usr/local/bin/tm-entrypoint"]
CMD ["coordinator"]

ARG TM_REVISION=unknown
LABEL org.opencontainers.image.title="Tournament Management" \
      org.opencontainers.image.revision=$TM_REVISION
