#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
case "${1:-}" in tm-worker-1|tm-worker-2) ;; *) die "Uso: bash scripts/fail-worker.sh tm-worker-1|tm-worker-2";; esac
require_docker
owned_container "$1"
docker kill --signal KILL "$1"
printf 'Worker %s detenido por fallo simulado. Recuperar: docker start %s\n' "$1" "$1"
