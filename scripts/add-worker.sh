#!/usr/bin/env bash
# Añade un worker al clúster en marcha (4.3), con la misma imagen y token que el
# coordinador. Uso: bash scripts/add-worker.sh tm-worker-N
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
name="${1:-}"
[[ "$name" =~ ^tm-worker-[0-9]+$ ]] || die "Uso: bash scripts/add-worker.sh tm-worker-N"
require_docker
if docker container inspect "$name" >/dev/null 2>&1; then
  die "Ya existe $name. Usa docker start $name si estaba detenido."
fi
owned_container tm-head
owned_container tm-coordinator
# Misma revisión que el resto del clúster, aunque la etiqueta haya cambiado.
image_id="$(docker container inspect --format '{{.Image}}' tm-coordinator)"
token="$(docker container inspect --format '{{range .Config.Env}}{{println .}}{{end}}' tm-coordinator |
  sed -n 's/^TM_INTERNAL_TOKEN=//p')"
[[ -n "$token" ]] || die "El coordinador no tiene TM_INTERNAL_TOKEN: recrea el clúster con start-cluster.sh."
start_worker "$name" "$image_id" "$token"
wait_for "$name disponible en /api/v1/workers" docker exec tm-coordinator python -c "
import json, sys, urllib.request
workers = json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/workers', timeout=5))
sys.exit(0 if any(w['worker_id'] == '$name' and w['status'] == 'available' for w in workers) else 1)"
printf 'Worker %s añadido.\n' "$name"
