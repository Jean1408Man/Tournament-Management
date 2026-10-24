#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_docker
image_id=""
for name in "${TM_CONTAINERS[@]}"; do
  owned_container "$name"
  current="$(docker container inspect --format '{{.Image}}' "$name")"
  if [[ "$name" != tm-postgres ]]; then
    if [[ -n "$image_id" && "$current" != "$image_id" ]]; then die "Imágenes de aplicación distintas."; fi
    image_id="$current"
  fi
  [[ "$(docker container inspect --format '{{.State.Running}}' "$name")" == true ]] ||
    die "$name no está ejecutándose."
  docker container inspect --format \
    '{{.Name}} image={{.Image}} memory={{.HostConfig.Memory}} cpus={{.HostConfig.NanoCpus}} shm={{.HostConfig.ShmSize}}' "$name"
done
printf 'Comprobando membresía mediante Ray Client (máximo 30 s)...\n'
# El plazo vive dentro del contenedor: no dejar Python huérfano al matar docker exec.
if docker exec tm-coordinator timeout --kill-after=2 30 python -m \
  tournament_management.adapters.ray_membership --address ray://tm-head:10001 --expected-workers 2; then
  :
else
  status=$?
  printf 'Falló la consulta de membresía Ray (código %s; 124 indica timeout).\n' "$status" >&2
  docker exec tm-head tail -40 /tmp/ray/session_latest/logs/ray_client_server.err >&2 || true
  exit "$status"
fi
docker exec tm-coordinator python -c \
  'import socket; print({n: socket.gethostbyname(n) for n in ("tm-postgres", "tm-head", "tm-worker-1", "tm-worker-2")})'
docker exec tm-coordinator tm migrate
docker exec tm-coordinator tm doctor --backend ray --http-host 0.0.0.0
printf 'Infraestructura comprobada. /ready debe dar 200 con PostgreSQL migrado y TM_BACKEND=local; Ray aún no ejecuta torneos.\n'
