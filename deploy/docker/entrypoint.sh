#!/usr/bin/env bash
set -euo pipefail

# Proceso hijo: pide que pare y espera a que termine. Uso: stop PID SEÑAL
stop() {
  [[ -n "$1" ]] || return 0
  kill "-$2" "$1" 2>/dev/null || true
  wait "$1" 2>/dev/null || true
}

# Coordinador con su PostgreSQL local (imagen coordinator, TM_PGDATA definido):
# solo socket Unix y sin puerto (L4). Si uno de los dos procesos termina, para
# el otro y el contenedor sale con error, para que el fallo se vea (L3).
serve_with_local_database() {
  local socket=/run/postgresql version exists pg_pid serve_pid="" status
  # PostgreSQL no se ejecuta como root; setpriv cambia de usuario sin quedar en medio.
  local as_postgres=(setpriv --reuid=postgres --regid=postgres --init-groups --)
  # Un volumen nuevo puede llegar con dueño root.
  chown postgres:postgres "$TM_PGDATA"
  chmod 700 "$TM_PGDATA"
  if [[ -f "$TM_PGDATA/PG_VERSION" ]]; then
    version="$(<"$TM_PGDATA/PG_VERSION")"
    if [[ "$version" != "$PG_MAJOR" ]]; then
      printf 'El volumen de la base tiene datos de PostgreSQL %s y esta imagen trae el %s.\n' \
        "$version" "$PG_MAJOR" >&2
      printf 'Respáldalo y elimínalo antes de arrancar (deploy/OPERATIONS.md); no se borra solo.\n' >&2
      exit 1
    fi
  else
    "${as_postgres[@]}" initdb -D "$TM_PGDATA" -U tm -A trust --encoding=UTF8
  fi
  "${as_postgres[@]}" postgres -D "$TM_PGDATA" -c listen_addresses='' -k "$socket" &
  pg_pid=$!
  trap 'stop "$serve_pid" TERM; stop "$pg_pid" INT; exit 0' TERM INT
  # -d postgres: sin él, pg_isready prueba la base "tm" y deja un FATAL en el log.
  for ((i = 0; i < 60; i++)); do
    pg_isready -q -h "$socket" -U tm -d postgres && break
    if ! kill -0 "$pg_pid" 2>/dev/null; then
      printf 'PostgreSQL terminó antes de aceptar conexiones.\n' >&2
      exit 1
    fi
    sleep 1
  done
  if ! pg_isready -q -h "$socket" -U tm -d postgres; then
    printf 'PostgreSQL no aceptó conexiones en 60 s.\n' >&2
    stop "$pg_pid" INT
    exit 1
  fi
  exists="$(psql -h "$socket" -U tm -d postgres -Atc \
    "SELECT 1 FROM pg_database WHERE datname = 'tournament_management'")"
  [[ "$exists" == 1 ]] || createdb -h "$socket" -U tm tournament_management
  tm migrate --database-url "$TM_DATABASE_URL"
  "$@" &
  serve_pid=$!
  status=0
  wait -n "$pg_pid" "$serve_pid" || status=$?
  if ! kill -0 "$pg_pid" 2>/dev/null; then
    printf 'PostgreSQL terminó (código %s): se detiene el coordinador.\n' "$status" >&2
    stop "$serve_pid" TERM
    exit 1
  fi
  # Terminó tm serve: la base se para después, para que nadie escriba sin ella.
  stop "$pg_pid" INT
  exit "$status"
}

role="${1:-coordinator}"
if (($#)); then shift; fi
case "$role" in
  coordinator)
    export TM_ROLE=coordinator
    serve=(tm serve --backend "${TM_BACKEND:-local}" --ray-address "${TM_RAY_ADDRESS:-ray://tm-head:10001}"
           --http-host 0.0.0.0 "$@")
    if [[ -n "${TM_PGDATA:-}" ]]; then
      serve_with_local_database "${serve[@]}"
    fi
    # Sin base local (pruebas, desarrollo): se usa TM_DATABASE_URL si la hay.
    if [[ -n "${TM_DATABASE_URL:-}" ]]; then
      tm migrate --database-url "$TM_DATABASE_URL"
    fi
    exec "${serve[@]}"
    ;;
  ray-head)
    exec ray start --head --port=6379 --ray-client-server-port=10001 \
      --num-cpus=0 --num-gpus=0 --include-dashboard=false \
      --object-store-memory="${TM_OBJECT_STORE_BYTES:-268435456}" \
      --disable-usage-stats --block "$@"
    ;;
  ray-worker)
    export TM_WORKER_LOG_PATH=/proc/1/fd/1
    # El nombre de nodo es la identidad pública del worker (hostname del contenedor).
    exec ray start --address="${TM_RAY_HEAD_ADDRESS:-tm-head:6379}" \
      --node-name="$(hostname)" \
      --num-cpus="${TM_WORKER_CPUS:-1}" --num-gpus=0 --resources='{"tm_worker": 1}' \
      --object-store-memory="${TM_OBJECT_STORE_BYTES:-268435456}" \
      --disable-usage-stats --block "$@"
    ;;
  *)
    printf 'Rol desconocido: %s. Use coordinator, ray-head o ray-worker.\n' "$role" >&2
    exit 2
    ;;
esac
