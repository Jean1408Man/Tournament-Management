#!/usr/bin/env bash
# Clúster sin Docker (etapa 4.4): PostgreSQL propio, head y workers de Ray y el
# coordinador como procesos de Linux/WSL. Mismos parámetros que start-cluster.sh.
# Los datos persisten en TM_LOCAL_HOME (~/.local/share/tournament-management)
# entre stop y start; solo `reset` los borra. Guía: deploy/ARRANQUE.md.
#
# Uso: bash scripts/local-cluster.sh start|stop|status|reset|add-worker N|
#      kill NOMBRE|pause NOMBRE|resume NOMBRE|restore NOMBRE|recover
# NOMBRE: head, coordinator o tm-worker-N.
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
LOCAL_HOME="${TM_LOCAL_HOME:-$HOME/.local/share/tournament-management}"
RUN="$LOCAL_HOME/run" LOGS="$LOCAL_HOME/logs" PGDATA_DIR="$LOCAL_HOME/postgres"
VENV="${TM_VENV:-$PROJECT_DIR/.venv}"
API_PORT="${TM_API_PORT:-8000}" CLIENT_PORT="${TM_LOCAL_RAY_CLIENT_PORT:-10001}"
PG_PORT="${TM_LOCAL_PG_PORT:-5433}" WORKERS="${TM_LOCAL_WORKERS:-${TM_WORKERS:-4}}"
DB_URL="postgresql:///tournament_management?host=$RUN&port=$PG_PORT&user=tm"
API="http://127.0.0.1:$API_PORT"
# Misma mitigación gRPC que el despliegue Docker (decisión 1.15).
export GRPC_POLL_STRATEGY=poll GRPC_ENABLE_FORK_SUPPORT=true RAY_USAGE_STATS_ENABLED=0

die() { printf '%s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }

[[ "$(uname -s)" == Linux ]] || die "El modo sin Docker necesita Linux o WSL (el árbitro usa llamadas de Linux)."
[[ -x "$VENV/bin/tm" ]] || die "Falta el entorno: ejecuta 'uv sync --locked --extra dev' (o define TM_VENV)."
mkdir -p "$RUN" "$LOGS"

pg_bin() {
  local dir
  dir=$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)
  [[ -n "$dir" ]] || die "Instala PostgreSQL (sudo apt install postgresql); solo se usan sus binarios."
  printf '%s' "$dir"
}

alive() { [[ -f "$RUN/$1.pid" ]] && kill -0 "$(cat "$RUN/$1.pid")" 2>/dev/null; }

tree() {  # pid → él y todos sus descendientes (un nodo Ray entero, como un contenedor)
  local pids=("$1") i=0 child
  while ((i < ${#pids[@]})); do
    for child in $(ps -eo pid=,ppid= | awk -v p="${pids[$i]}" '$2 == p {print $1}'); do
      pids+=("$child")
    done
    i=$((i + 1))
  done
  printf '%s\n' "${pids[@]}"
}

signal_tree() {  # señal nombre
  alive "$2" || return 0
  # shellcheck disable=SC2046
  kill "-$1" $(tree "$(cat "$RUN/$2.pid")") 2>/dev/null || true
}

start_bg() {  # nombre comando... → proceso en su propia sesión, con log y pid
  local name="$1"; shift
  setsid nohup "$@" >> "$LOGS/$name.log" 2>&1 < /dev/null &
  echo $! > "$RUN/$name.pid"
}

wait_for() {  # descripción segundos [proceso-vigilado] comando...
  local what="$1" seconds="$2" watched="" deadline; shift 2
  if [[ "${1:-}" == --watch ]]; then watched="$2"; shift 2; fi
  deadline=$((SECONDS + seconds))
  until "$@" >/dev/null 2>&1; do
    ((SECONDS < deadline)) || die "Tiempo agotado: $what (ver $LOGS)"
    # Si el proceso que debía quedar listo ya murió, no tiene sentido esperar.
    if [[ -n "$watched" ]] && ! alive "$watched"; then return 1; fi
    sleep 1
  done
  say "Disponible: $what"
}

postgres_start() {
  local bin
  bin=$(pg_bin)
  if [[ ! -f "$PGDATA_DIR/PG_VERSION" ]]; then
    "$bin/initdb" -D "$PGDATA_DIR" -U tm -A trust --encoding=UTF8 > "$LOGS/initdb.log"
  fi
  if ! "$bin/pg_ctl" -D "$PGDATA_DIR" status >/dev/null 2>&1; then
    # Solo socket Unix en RUN: no abre puertos TCP ni choca con otro PostgreSQL.
    "$bin/pg_ctl" -D "$PGDATA_DIR" -l "$LOGS/postgres.log" -w \
      -o "-k $RUN -c listen_addresses='' -p $PG_PORT" start > /dev/null
  fi
  if ! "$bin/psql" -h "$RUN" -p "$PG_PORT" -U tm -d postgres -Atc \
      "SELECT 1 FROM pg_database WHERE datname = 'tournament_management'" | grep -q 1; then
    "$bin/createdb" -h "$RUN" -p "$PG_PORT" -U tm tournament_management
  fi
  "$VENV/bin/tm" migrate --database-url "$DB_URL" > /dev/null
  say "Disponible: PostgreSQL (datos en $PGDATA_DIR)"
}

clean_ray() {
  # Restos de un arranque anterior (procesos de Ray sin dueño o la sesión vieja de
  # /tmp/ray) hicieron fallar el registro de un worker al volver a arrancar.
  "$VENV/bin/ray" stop --force > /dev/null 2>&1 || true
  rm -rf /tmp/ray/session_latest
}

head_start() {
  rm -f "$RUN/head.address"
  : > "$LOGS/head.log"
  start_bg head "$VENV/bin/python" scripts/local_cluster.py head \
    --client-port "$CLIENT_PORT" --address-file "$RUN/head.address"
  wait_for "head de Ray" 120 --watch head test -s "$RUN/head.address" ||
    die "El head de Ray terminó al arrancar (ver $LOGS/head.log)"
}

worker_start() {  # nombre
  [[ "$1" =~ ^tm-worker-[0-9]+$ ]] || die "Nombre de worker inválido: $1"
  alive "$1" && { say "$1 ya está en marcha"; return 0; }
  local attempt
  for attempt in 1 2; do
    : > "$LOGS/$1.log"
    # Sin volumen compartido: descarga los bots del coordinador con el token (4.2).
    TM_ARTIFACT_URL="$API" TM_INTERNAL_TOKEN="$(cat "$RUN/token")" \
      start_bg "$1" "$VENV/bin/python" scripts/local_cluster.py worker \
      --name "$1" --address-file "$RUN/head.address"
    wait_for "$1" 120 --watch "$1" grep -q "listo worker $1" "$LOGS/$1.log" && return 0
    say "$1 terminó al registrarse (intento $attempt): $(grep -m1 -E 'Error|error' "$LOGS/$1.log" | cut -c1-160)"
    stop_one "$1"
  done
  die "$1 no pudo registrarse en el head (ver $LOGS/$1.log)"
}

coordinator_start() {
  TM_DATABASE_URL="$DB_URL" TM_DATA_DIR="$LOCAL_HOME/data" TM_BACKEND=ray \
    TM_RAY_ADDRESS="ray://127.0.0.1:$CLIENT_PORT" TM_INTERNAL_TOKEN="$(cat "$RUN/token")" \
    TM_HTTP_HOST=127.0.0.1 TM_HTTP_PORT="$API_PORT" \
    start_bg coordinator "$VENV/bin/tm" serve
  wait_for "API del coordinador en $API" 180 --watch coordinator curl -sf "$API/ready" ||
    die "El coordinador terminó al arrancar (ver $LOGS/coordinator.log)"
}

topology() {  # número de workers esperado
  "$VENV/bin/python" -m tournament_management.cluster.ray_topology \
    --address "ray://127.0.0.1:$CLIENT_PORT" --expected-workers "$1"
}

stop_one() {  # nombre: TERM al proceso y sus descendientes; KILL si no termina
  alive "$1" || { rm -f "$RUN/$1.pid"; return 0; }
  signal_tree CONT "$1"   # por si estaba pausado
  signal_tree TERM "$1"
  for _ in $(seq 1 15); do alive "$1" || break; sleep 1; done
  signal_tree KILL "$1"
  rm -f "$RUN/$1.pid"
}

workers_running() { for f in "$RUN"/tm-worker-*.pid; do [[ -e "$f" ]] && basename "$f" .pid; done; }

stop_all() {
  stop_one coordinator
  for name in $(workers_running); do stop_one "$name"; done
  stop_one head
  clean_ray
  local bin
  bin=$(pg_bin)
  "$bin/pg_ctl" -D "$PGDATA_DIR" status >/dev/null 2>&1 &&
    "$bin/pg_ctl" -D "$PGDATA_DIR" -m fast -w stop > /dev/null
  say "Detenido. Los datos se conservan en $LOCAL_HOME."
}

case "${1:-}" in
  start)
    curl -s "$API/health" >/dev/null 2>&1 &&
      die "Ya hay una API en $API (¿clúster Docker o local en marcha?). Detenlo antes."
    postgres_start
    od -An -N32 -tx1 /dev/urandom | tr -d ' \n' > "$RUN/token"
    chmod 600 "$RUN/token"
    clean_ray
    head_start
    for i in $(seq 1 "$WORKERS"); do worker_start "tm-worker-$i"; done
    wait_for "$WORKERS workers registrados" 120 topology "$WORKERS"
    coordinator_start
    "$0" status
    ;;
  stop) stop_all ;;
  status)
    for name in head $(workers_running) coordinator; do
      printf '%-14s %s\n' "$name" "$(alive "$name" && echo "en marcha (pid $(cat "$RUN/$name.pid"))" || echo detenido)"
    done
    printf '%-14s %s\n' ready "$(curl -s -o /dev/null -w '%{http_code}' "$API/ready" || true)"
    printf '%-14s %s\n' workers "$(curl -s "$API/api/v1/workers" || true)"
    ;;
  reset)
    if [[ "${2:-}" != --yes ]]; then
      read -r -p "Se borrarán bots, torneos y la base en $LOCAL_HOME. Escribe 'borrar': " answer
      [[ "$answer" == borrar ]] || die "Cancelado."
    fi
    stop_all
    rm -rf "$LOCAL_HOME"
    say "Datos locales borrados."
    ;;
  add-worker) [[ -n "${2:-}" ]] || die "Uso: add-worker N"; worker_start "tm-worker-$2" ;;
  kill) signal_tree KILL "${2:?nombre}"; rm -f "$RUN/$2.pid"; say "Matado $2" ;;
  pause) signal_tree STOP "${2:?nombre}"; say "Pausado $2" ;;
  resume) signal_tree CONT "${2:?nombre}"; say "Reanudado $2" ;;
  restore)
    name="${2:?nombre}"
    case "$name" in
      head) alive head || head_start ;;
      coordinator) alive coordinator || coordinator_start ;;
      *) worker_start "$name" ;;
    esac
    ;;
  recover)
    # Equivale al procedimiento de OPERATIONS.md tras perder el head: head nuevo,
    # workers que se conectan a él y coordinador reiniciado (recuperación de 3.4).
    names=$(workers_running); [[ -n "$names" ]] || names=$(seq -f 'tm-worker-%g' 1 "$WORKERS")
    stop_one coordinator
    for name in $names; do stop_one "$name"; done
    stop_one head
    clean_ray
    head_start
    for name in $names; do worker_start "$name"; done
    coordinator_start
    ;;
  *) sed -n '2,10p' "$0"; exit 2 ;;
esac
