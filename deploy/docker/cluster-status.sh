#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_docker
image_id=""
mapfile -t workers < <(cluster_workers)
for name in tm-head "${workers[@]}" tm-coordinator; do
  owned_container "$name"
  current="$(docker container inspect --format '{{.Image}}' "$name")"
  # Head y workers comparten imagen; el coordinador usa la suya, con la base (L2).
  if [[ "$name" == tm-coordinator ]]; then
    coordinator_image="$current"
  else
    if [[ -n "$image_id" && "$current" != "$image_id" ]]; then die "Head y workers con imágenes distintas."; fi
    image_id="$current"
  fi
  [[ "$(docker container inspect --format '{{.State.Running}}' "$name")" == true ]] ||
    die "$name no está ejecutándose."
  docker container inspect --format \
    '{{.Name}} image={{.Image}} memory={{.HostConfig.Memory}} cpus={{.HostConfig.NanoCpus}} shm={{.HostConfig.ShmSize}}' "$name"
done
revision() {
  docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$1"
}
[[ "$(revision "$image_id")" == "$(revision "$coordinator_image")" ]] ||
  die "El coordinador y los workers vienen de revisiones distintas."
printf 'Comprobando membresía mediante Ray Client (máximo 30 s)...\n'
# El plazo vive dentro del contenedor: no dejar Python huérfano al matar docker exec.
if docker exec tm-coordinator timeout --kill-after=2 30 python -m \
  tournament_management.cluster.ray_topology --address ray://tm-head:10001 \
  --expected-workers "${#workers[@]}"; then
  :
else
  status=$?
  printf 'Falló la consulta de membresía Ray (código %s; 124 indica timeout).\n' "$status" >&2
  docker exec tm-head tail -40 /tmp/ray/session_latest/logs/ray_client_server.err >&2 || true
  exit "$status"
fi
docker exec tm-coordinator python -c \
  'import socket, sys; print({n: socket.gethostbyname(n) for n in sys.argv[1:]})' \
  tm-head "${workers[@]}"
# La base es local al coordinador: responde por socket, no escucha en la red y
# ningún otro nodo tiene PostgreSQL instalado.
docker exec tm-coordinator pg_isready -h /run/postgresql -U tm -d tournament_management
listen="$(docker exec tm-coordinator psql -h /run/postgresql -U tm -d tournament_management \
  -Atc 'SHOW listen_addresses')"
[[ -z "$listen" ]] || die "PostgreSQL escucha en la red ($listen): debe atender solo por socket."
for name in tm-head "${workers[@]}"; do
  docker exec "$name" test ! -e /usr/lib/postgresql ||
    die "$name tiene PostgreSQL instalado: debe usar la imagen sin base."
done
docker exec tm-coordinator tm migrate
docker exec tm-coordinator tm doctor --backend ray --http-host 0.0.0.0
printf 'Infraestructura comprobada. /ready debe dar 200 con la base local del coordinador migrada; con TM_BACKEND=ray las partidas se ejecutan en los workers.\n'
