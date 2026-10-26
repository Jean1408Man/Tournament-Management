#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_docker
[[ "$TM_POSTGRES_PASSWORD" =~ ^[a-zA-Z0-9]+$ ]] ||
  die "Configura TM_POSTGRES_PASSWORD en .env usando solo letras y números."
[[ "${TM_BACKEND:-local}" == local || "${TM_BACKEND:-local}" == ray ]] || die "TM_BACKEND debe ser local o ray."
command -v timeout >/dev/null || die "Se requiere timeout (GNU coreutils)."
[[ "$TM_ARTIFACT_MAX_BYTES" =~ ^[1-9][0-9]*$ ]] || die "TM_ARTIFACT_MAX_BYTES debe ser positivo."
[[ "$TM_START_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || die "TM_START_TIMEOUT debe ser positivo."
image_id="$(docker image inspect --format '{{.Id}}' "$TM_IMAGE")" ||
  die "Imagen local ausente. Ejecuta bash scripts/build-image.sh."
for name in "${TM_CONTAINERS[@]}"; do
  if docker container inspect "$name" >/dev/null 2>&1; then
    die "Ya existe $name. Revisa el clúster y usa stop-cluster.sh antes de recrearlo."
  fi
done
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

# Un volumen dedicado permite compartir solo ejecutables, sin exponer el resto
# del estado del coordinador. Los workers reciben exclusivamente lectura.
if docker volume inspect tm-artifacts >/dev/null 2>&1; then
  owned volume tm-artifacts
else
  docker volume create --label "$TM_LABEL_KEY=$TM_LABEL_VALUE" tm-artifacts
fi

trap 'status=$?; printf "Arranque falló (código %s), línea %s: %s\n" "$status" "$LINENO" "$BASH_COMMAND" >&2; diagnostics; exit "$status"' ERR
base=(--detach --pull never --init --network tm-net --label "$TM_LABEL_KEY=$TM_LABEL_VALUE"
      --stop-timeout 20 --log-opt max-size=10m --log-opt max-file=3
      --env GRPC_POLL_STRATEGY=poll --env GRPC_ENABLE_FORK_SUPPORT=true
      --env "TM_ARTIFACT_MAX_BYTES=$TM_ARTIFACT_MAX_BYTES")
ray_limits=(--cpus 1 --memory 2g --shm-size 512m --ulimit nofile=65536:65536)

docker image inspect "$TM_POSTGRES_IMAGE" >/dev/null 2>&1 ||
  die "Imagen PostgreSQL ausente ($TM_POSTGRES_IMAGE). Descárgala explícitamente antes de arrancar."
docker run "${base[@]}" --name tm-postgres --hostname tm-postgres \
  --mount source=tm-postgres-data,target=/var/lib/postgresql/data \
  --env POSTGRES_DB=tournament_management --env POSTGRES_USER=tm \
  --env POSTGRES_PASSWORD="$TM_POSTGRES_PASSWORD" "$TM_POSTGRES_IMAGE"
wait_for "PostgreSQL" docker exec tm-postgres pg_isready -U tm -d tournament_management

docker run "${base[@]}" "${ray_limits[@]}" --name tm-head --hostname tm-head \
  "$image_id" ray-head
wait_for "control de Ray" docker exec tm-head timeout --kill-after=2 12 \
  ray health-check --address=127.0.0.1:6379

for name in tm-worker-1 tm-worker-2; do
  docker run "${base[@]}" "${ray_limits[@]}" --name "$name" --hostname "$name" \
    --mount source=tm-artifacts,target=/var/lib/tm/artifacts,readonly \
    "$image_id" ray-worker
done
wait_for "dos workers registrados" docker exec tm-head timeout --kill-after=2 12 python -m \
  tournament_management.cluster.ray_topology --address ray://tm-head:10001 --expected-workers 2

docker run "${base[@]}" --name tm-coordinator --hostname tm-coordinator \
  --cpus 1 --memory 1g --publish "127.0.0.1:${TM_API_PORT:-8000}:8000" \
  --mount source=tm-state,target=/var/lib/tm \
  --mount source=tm-artifacts,target=/var/lib/tm/artifacts \
  --env TM_RAY_ADDRESS=ray://tm-head:10001 \
  --env "TM_DATABASE_URL=postgresql://tm:${TM_POSTGRES_PASSWORD}@tm-postgres:5432/tournament_management" \
  --env "TM_BACKEND=${TM_BACKEND:-local}" --env TM_HTTP_HOST=0.0.0.0 \
  "$image_id" coordinator
wait_for "API del coordinador" docker exec tm-coordinator python -c \
  'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2).read()'
bash "$TM_SCRIPT_DIR/cluster-status.sh"
