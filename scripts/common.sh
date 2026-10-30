#!/usr/bin/env bash
# Biblioteca compartida; no inicia servicios al cargarla.
set -euo pipefail

TM_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TM_PROJECT_DIR="$(cd -- "$TM_SCRIPT_DIR/.." && pwd)"
# Leer solo datos, sin ejecutar el archivo como código shell. El entorno prevalece.
TM_ENV_FILE="${TM_ENV_FILE:-$TM_PROJECT_DIR/.env}"
if [[ -f "$TM_ENV_FILE" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    if [[ "$line" =~ ^(TM_IMAGE|TM_POSTGRES_IMAGE|TM_POSTGRES_PASSWORD|TM_API_PORT|TM_START_TIMEOUT|TM_ARTIFACT_MAX_BYTES|TM_BACKEND|TM_WORKERS|TM_WORKER_MEMORY|TM_MAX_RUNNING_ATTEMPTS|TM_MAX_ACTIVE_TOURNAMENTS)=(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      value="${BASH_REMATCH[2]}"
      if [[ ! -v "$key" ]]; then
        printf -v "$key" '%s' "$value"
        export "$key"
      fi
    fi
  done < "$TM_ENV_FILE"
fi
TM_IMAGE="${TM_IMAGE:-tournament-management:step-1.5}"
TM_LABEL_KEY="org.tournament-management.project"
TM_LABEL_VALUE="tournament-management"
# Número de workers (5.1, E1.12): un único valor genera tm-worker-1..N.
TM_WORKERS="${TM_WORKERS:-4}"
TM_WORKER_MEMORY="${TM_WORKER_MEMORY:-2g}"
if [[ ! "$TM_WORKERS" =~ ^[1-9][0-9]*$ || ! "$TM_WORKER_MEMORY" =~ ^[1-9][0-9]*[kmgKMG]?$ ]]; then
  printf 'TM_WORKERS debe ser un entero positivo y TM_WORKER_MEMORY un tamaño (2g, 1536m).\n' >&2
  exit 1
fi
TM_WORKER_NAMES=()
for ((i = 1; i <= TM_WORKERS; i++)); do TM_WORKER_NAMES+=("tm-worker-$i"); done
TM_CONTAINERS=(tm-postgres tm-head "${TM_WORKER_NAMES[@]}" tm-coordinator)
TM_POSTGRES_IMAGE="${TM_POSTGRES_IMAGE:-postgres:16.6-bookworm}"
TM_POSTGRES_PASSWORD="${TM_POSTGRES_PASSWORD:-}"
TM_START_TIMEOUT="${TM_START_TIMEOUT:-180}"
TM_ARTIFACT_MAX_BYTES="${TM_ARTIFACT_MAX_BYTES:-1048576}"

TM_RUN_BASE=(--detach --pull never --init --network tm-net --label "$TM_LABEL_KEY=$TM_LABEL_VALUE"
             --stop-timeout 20 --log-opt max-size=10m --log-opt max-file=3
             --env GRPC_POLL_STRATEGY=poll --env GRPC_ENABLE_FORK_SUPPORT=true
             --env "TM_ARTIFACT_MAX_BYTES=$TM_ARTIFACT_MAX_BYTES")
TM_RAY_LIMITS=(--cpus 1 --memory 2g --shm-size 512m --ulimit nofile=65536:65536)
# Un hueco por worker (E1.5): una CPU por partida para que los plazos sean justos.
TM_WORKER_LIMITS=(--cpus 1 --memory "$TM_WORKER_MEMORY" --shm-size 512m
                  --ulimit nofile=65536:65536)

die() { printf '%s\n' "$*" >&2; exit 1; }

# Worker Ray sin volumen ni credenciales PostgreSQL: descarga los bots del
# coordinador con el token (4.2). Lo usan start-cluster.sh y add-worker.sh.
start_worker() {
  local name="$1" image="$2" token="$3"
  docker run "${TM_RUN_BASE[@]}" "${TM_WORKER_LIMITS[@]}" --name "$name" --hostname "$name" \
    --env TM_ARTIFACT_URL=http://tm-coordinator:8000 \
    --env "TM_INTERNAL_TOKEN=$token" \
    "$image" ray-worker
}

# Workers configurados más los añadidos con add-worker.sh, en orden numérico.
cluster_workers() {
  {
    printf '%s\n' "${TM_WORKER_NAMES[@]}"
    docker ps -a --filter "label=$TM_LABEL_KEY=$TM_LABEL_VALUE" --format '{{.Names}}' |
      grep -E '^tm-worker-[0-9]+$' || true
  } | sort -u -t- -k3,3n
}

require_docker() {
  command -v docker >/dev/null || die "Docker no está instalado."
  docker info >/dev/null || die "No se puede acceder al daemon Docker."
}

owned() {
  local kind="$1" name="$2" label
  label="$(docker "$kind" inspect --format "{{index .Labels \"$TM_LABEL_KEY\"}}" "$name")"
  [[ "$label" == "$TM_LABEL_VALUE" ]] || die "$name existe pero no pertenece a este proyecto."
}

owned_container() {
  local label
  label="$(docker container inspect --format "{{index .Config.Labels \"$TM_LABEL_KEY\"}}" "$1")"
  [[ "$label" == "$TM_LABEL_VALUE" ]] || die "$1 no pertenece a este proyecto."
}

diagnostics() {
  local name
  for name in "${TM_CONTAINERS[@]}"; do
    if docker container inspect "$name" >/dev/null 2>&1; then
      printf '\n--- %s ---\n' "$name" >&2
      docker logs --tail 40 "$name" >&2 || true
    fi
  done
}

wait_for() {
  local description="$1"; shift
  local deadline=$((SECONDS + TM_START_TIMEOUT))
  while ((SECONDS < deadline)); do
    if timeout --kill-after=2 "${TM_TRY_TIMEOUT:-15}" "$@" >/dev/null 2>&1; then
      printf 'Disponible: %s\n' "$description"
      return 0
    fi
    sleep 2
  done
  printf 'Tiempo agotado: %s\n' "$description" >&2
  return 1
}
