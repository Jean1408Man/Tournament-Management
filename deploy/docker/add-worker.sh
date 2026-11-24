#!/usr/bin/env bash
# Añade un worker al clúster en marcha, con la misma imagen y token que el
# coordinador. Uso: bash deploy/docker/add-worker.sh tm-worker-N
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
name="${1:-}"
[[ "$name" =~ ^tm-worker-[0-9]+$ ]] || die "Uso: bash deploy/docker/add-worker.sh tm-worker-N"
require_docker
if docker container inspect "$name" >/dev/null 2>&1; then
  die "Ya existe $name. Usa docker start $name si estaba detenido."
fi
owned_container tm-head
owned_container tm-coordinator
# La imagen del head (la de los workers, sin PostgreSQL): misma revisión que el
# resto del clúster, aunque la etiqueta haya cambiado.
image_id="$(docker container inspect --format '{{.Image}}' tm-head)"
token="$(docker container inspect --format '{{range .Config.Env}}{{println .}}{{end}}' tm-coordinator |
  sed -n 's/^TM_INTERNAL_TOKEN=//p')"
[[ -n "$token" ]] || die "El coordinador no tiene TM_INTERNAL_TOKEN: recrea el clúster con start-cluster.sh."
# La espera del clúster en marcha, no la del entorno: el plazo del coordinador cuenta con ella.
delay="$(docker container inspect --format '{{range .Config.Env}}{{println .}}{{end}}' tm-coordinator |
  sed -n 's/^TM_WORKER_DELAY_MS=//p')"
start_worker "$name" "$image_id" "$token" "${delay:-0}"
wait_for "$name disponible en /api/v1/workers" docker exec tm-coordinator python -c "
import json, sys, urllib.request
workers = json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/workers', timeout=5))
sys.exit(0 if any(w['worker_id'] == '$name' and w['status'] == 'available' for w in workers) else 1)"
printf 'Worker %s añadido.\n' "$name"
