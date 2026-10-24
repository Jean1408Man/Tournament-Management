#!/usr/bin/env bash
# Verifica el mismo objeto en cada worker sin acceso a PostgreSQL ni a la API.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
[[ $# == 1 && "$1" =~ ^[0-9a-f]{64}$ ]] || die "Uso: bash scripts/probe-artifact.sh SHA256"
require_docker
for name in tm-worker-1 tm-worker-2; do
  owned_container "$name"
  writable="$(docker container inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/tm/artifacts"}}{{.RW}}{{end}}{{end}}' "$name")"
  [[ "$writable" == false ]] || die "$name debe montar tm-artifacts en solo lectura."
  printf 'Verificando artefacto en %s...\n' "$name"
  docker exec "$name" tm verify-artifact --artifact-hash "$1"
done
