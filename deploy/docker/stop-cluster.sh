#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_docker
# Validar todos los objetivos antes de detener cualquiera.
targets=()
# También los añadidos con add-worker.sh (4.3): con un token viejo se
# reconectarían al head del próximo arranque.
mapfile -t workers < <(cluster_workers | sort -t- -k3,3nr)
for name in tm-coordinator "${workers[@]}" tm-head tm-postgres; do
  if docker container inspect "$name" >/dev/null 2>&1; then
    owned_container "$name"
    targets+=("$name")
  fi
done
for name in "${targets[@]}"; do
  docker stop --timeout 20 "$name"
  docker container rm "$name"
done
printf 'Contenedores retirados. tm-state, tm-postgres-data, tm-artifacts y tm-net se conservan para el próximo arranque.\n'
