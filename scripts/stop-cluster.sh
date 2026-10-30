#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_docker
# Validar todos los objetivos antes de detener cualquiera.
targets=()
# Workers añadidos con add-worker.sh (4.3): con un token viejo se reconectarían
# al head del próximo arranque, así que se retiran también.
extra=()
while IFS= read -r name; do
  [[ "$name" =~ ^tm-worker-[0-9]+$ && "$name" != tm-worker-1 && "$name" != tm-worker-2 ]] &&
    extra+=("$name")
done < <(docker ps -a --filter "label=$TM_LABEL_KEY=$TM_LABEL_VALUE" --format '{{.Names}}')
for name in tm-coordinator "${extra[@]}" tm-worker-2 tm-worker-1 tm-head tm-postgres; do
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
