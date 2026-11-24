#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_docker
revision="$(git -C "$TM_PROJECT_DIR" rev-parse --short HEAD)"
if [[ -n "$(git -C "$TM_PROJECT_DIR" status --porcelain)" ]]; then revision="$revision-dirty"; fi
# Este es el único script que permite descargas: base, uv, dependencias y PostgreSQL.
build() {  # etapa del Dockerfile, etiqueta
  docker build --file "$TM_PROJECT_DIR/deploy/docker/Dockerfile" --target "$1" \
    --build-arg "TM_REVISION=$revision" --tag "$2" "$TM_PROJECT_DIR"
  docker image inspect --format "Imagen $1 construida: {{.Id}}" "$2"
}
# Head y workers sin PostgreSQL; el coordinador con su base local (L2).
build worker "$TM_IMAGE"
build coordinator "$TM_COORDINATOR_IMAGE"
