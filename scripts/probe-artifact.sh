#!/usr/bin/env bash
# Comprueba que cada worker obtiene el objeto descargándolo del coordinador (4.2),
# sin volumen compartido ni acceso a PostgreSQL.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
[[ $# == 1 && "$1" =~ ^[0-9a-f]{64}$ ]] || die "Uso: bash scripts/probe-artifact.sh SHA256"
require_docker
for name in tm-worker-1 tm-worker-2; do
  owned_container "$name"
  mounted="$(docker container inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/tm/artifacts"}}yes{{end}}{{end}}' "$name")"
  [[ -z "$mounted" ]] || die "$name no debe montar tm-artifacts: debe descargar del coordinador."
  printf 'Descargando y verificando artefacto en %s...\n' "$name"
  docker exec "$name" tm verify-artifact --artifact-hash "$1"
done
