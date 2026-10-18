#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_docker
revision="$(git -C "$TM_PROJECT_DIR" rev-parse --short HEAD)"
if [[ -n "$(git -C "$TM_PROJECT_DIR" status --porcelain)" ]]; then revision="$revision-dirty"; fi
# Este es el único script que permite descargas: base, uv y dependencias.
docker build --file "$TM_PROJECT_DIR/deploy/Dockerfile" \
  --build-arg "TM_REVISION=$revision" --tag "$TM_IMAGE" "$TM_PROJECT_DIR"
docker image inspect --format 'Imagen construida: {{.Id}}' "$TM_IMAGE"
