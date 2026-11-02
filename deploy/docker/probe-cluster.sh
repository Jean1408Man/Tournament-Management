#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_docker
owned_container tm-coordinator
# Plazo global dentro del contenedor; incluye conexión, consulta y ejecución.
# No usa exec -t: stdout contiene JSON y puede guardarse como evidencia.
if docker exec tm-coordinator timeout --kill-after=5 120 \
  tm probe-cluster --ray-address ray://tm-head:10001 \
  --expected-workers "$(cluster_workers | wc -l)" --timeout-seconds 30; then
  :
else
  status=$?
  printf 'Prueba remota fallida (código %s; 124 indica plazo global agotado).\n' "$status" >&2
  exit "$status"
fi
