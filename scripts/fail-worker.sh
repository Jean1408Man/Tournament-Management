#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
[[ "${1:-}" =~ ^tm-worker-[0-9]+$ ]] || die "Uso: bash scripts/fail-worker.sh tm-worker-N"
require_docker
owned_container "$1"
docker kill --signal KILL "$1"
printf 'Worker %s detenido por fallo simulado. Recuperar: docker start %s\n' "$1" "$1"
