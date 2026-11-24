#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_docker
[[ "${TM_BACKEND:-local}" == local || "${TM_BACKEND:-local}" == ray ]] || die "TM_BACKEND debe ser local o ray."
command -v timeout >/dev/null || die "Se requiere timeout (GNU coreutils)."
[[ "$TM_ARTIFACT_MAX_BYTES" =~ ^[1-9][0-9]*$ ]] || die "TM_ARTIFACT_MAX_BYTES debe ser positivo."
[[ "$TM_START_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || die "TM_START_TIMEOUT debe ser positivo."
# Token de la descarga interna de bots: nuevo en cada arranque salvo que
# ya esté exportado. No se guarda en .env; coordinador y workers lo comparten.
if [[ -z "${TM_INTERNAL_TOKEN:-}" ]]; then
  TM_INTERNAL_TOKEN="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
fi
[[ "$TM_INTERNAL_TOKEN" =~ ^[A-Za-z0-9]{16,}$ ]] ||
  die "TM_INTERNAL_TOKEN debe tener al menos 16 letras o números."
image_id="$(docker image inspect --format '{{.Id}}' "$TM_IMAGE")" ||
  die "Imagen local ausente. Ejecuta bash deploy/docker/build-image.sh."
coordinator_image_id="$(docker image inspect --format '{{.Id}}' "$TM_COORDINATOR_IMAGE")" ||
  die "Imagen del coordinador ausente. Ejecuta bash deploy/docker/build-image.sh."
for name in "${TM_CONTAINERS[@]}"; do
  if docker container inspect "$name" >/dev/null 2>&1; then
    die "Ya existe $name. Revisa el clúster y usa stop-cluster.sh antes de recrearlo."
  fi
done
if docker container inspect tm-postgres >/dev/null 2>&1; then
  die "Queda tm-postgres de una versión anterior. Retíralo con stop-cluster.sh."
fi
if docker network inspect tm-net >/dev/null 2>&1; then
  owned network tm-net
  [[ "$(docker network inspect --format '{{.Driver}}' tm-net)" == bridge ]] ||
    die "tm-net debe ser bridge."
else
  docker network create --driver bridge --label "$TM_LABEL_KEY=$TM_LABEL_VALUE" tm-net
fi
if docker volume inspect tm-state >/dev/null 2>&1; then
  owned volume tm-state
else
  docker volume create --label "$TM_LABEL_KEY=$TM_LABEL_VALUE" tm-state
fi
if docker volume inspect tm-postgres-data >/dev/null 2>&1; then
  owned volume tm-postgres-data
else
  docker volume create --label "$TM_LABEL_KEY=$TM_LABEL_VALUE" tm-postgres-data
fi

# Volumen de ejecutables del coordinador. Los workers no lo montan:
# descargan cada bot por hash desde el coordinador y verifican sus bytes.
if docker volume inspect tm-artifacts >/dev/null 2>&1; then
  owned volume tm-artifacts
else
  docker volume create --label "$TM_LABEL_KEY=$TM_LABEL_VALUE" tm-artifacts
fi

trap 'status=$?; printf "Arranque falló (código %s), línea %s: %s\n" "$status" "$LINENO" "$BASH_COMMAND" >&2; diagnostics; exit "$status"' ERR
base=("${TM_RUN_BASE[@]}")
ray_limits=("${TM_RAY_LIMITS[@]}")

docker run "${base[@]}" "${ray_limits[@]}" --name tm-head --hostname tm-head \
  "$image_id" ray-head
wait_for "control de Ray" docker exec tm-head timeout --kill-after=2 12 \
  ray health-check --address=127.0.0.1:6379

for name in "${TM_WORKER_NAMES[@]}"; do
  start_worker "$name" "$image_id" "$TM_INTERNAL_TOKEN"
done
# Cada conexión Ray Client arranca un proceso propio en el head; con el equipo
# cargado tarda varios segundos, así que el intento tiene plazo propio de 30 s.
TM_TRY_TIMEOUT=35 wait_for "$TM_WORKERS workers registrados" docker exec tm-head \
  timeout --kill-after=2 30 python -m \
  tournament_management.cluster.ray_topology --address ray://tm-head:10001 \
  --expected-workers "$TM_WORKERS"

# Límites de carga: solo se pasan si se definen (si no, los de la aplicación).
limits=()
for key in TM_MAX_RUNNING_ATTEMPTS TM_MAX_ACTIVE_TOURNAMENTS; do
  if [[ -n "${!key:-}" ]]; then limits+=(--env "$key=${!key}"); fi
done
# PostgreSQL corre dentro del coordinador, solo por socket y sin puerto: la
# imagen fija TM_DATABASE_URL y la ruta de datos. 2 CPU y 2 GiB porque la base
# comparte el contenedor con la API y el coordinador (L5).
docker run "${base[@]}" "${limits[@]}" --name tm-coordinator --hostname tm-coordinator \
  --cpus 2 --memory 2g --publish "127.0.0.1:${TM_API_PORT:-8000}:8000" \
  --mount source=tm-state,target=/var/lib/tm \
  --mount source=tm-artifacts,target=/var/lib/tm/artifacts \
  --mount source=tm-postgres-data,target=/var/lib/postgresql/data \
  --env TM_RAY_ADDRESS=ray://tm-head:10001 \
  --env "TM_BACKEND=${TM_BACKEND:-local}" --env TM_HTTP_HOST=0.0.0.0 \
  --env "TM_INTERNAL_TOKEN=$TM_INTERNAL_TOKEN" --env "TM_WORKER_DELAY_MS=$TM_WORKER_DELAY_MS" \
  "$coordinator_image_id" coordinator
wait_for "API del coordinador" docker exec tm-coordinator python -c \
  'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2).read()'
bash "$TM_SCRIPT_DIR/cluster-status.sh"
