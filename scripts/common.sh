#!/usr/bin/env bash
# Biblioteca compartida; no inicia servicios al cargarla.
set -euo pipefail

TM_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TM_PROJECT_DIR="$(cd -- "$TM_SCRIPT_DIR/.." && pwd)"
# Leer solo datos, sin ejecutar el archivo como código shell. El entorno prevalece.
TM_ENV_FILE="${TM_ENV_FILE:-$TM_PROJECT_DIR/.env}"
if [[ -f "$TM_ENV_FILE" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    if [[ "$line" =~ ^(TM_IMAGE|TM_POSTGRES_IMAGE|TM_POSTGRES_PASSWORD|TM_API_PORT|TM_START_TIMEOUT)=(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      value="${BASH_REMATCH[2]}"
      if [[ ! -v "$key" ]]; then
        printf -v "$key" '%s' "$value"
        export "$key"
      fi
    fi
  done < "$TM_ENV_FILE"
fi
TM_IMAGE="${TM_IMAGE:-tournament-management:step-1.5}"
TM_LABEL_KEY="org.tournament-management.project"
TM_LABEL_VALUE="tournament-management"
TM_CONTAINERS=(tm-postgres tm-head tm-worker-1 tm-worker-2 tm-coordinator)
TM_POSTGRES_IMAGE="${TM_POSTGRES_IMAGE:-postgres:16.6-bookworm}"
TM_POSTGRES_PASSWORD="${TM_POSTGRES_PASSWORD:-}"
TM_START_TIMEOUT="${TM_START_TIMEOUT:-180}"

die() { printf '%s\n' "$*" >&2; exit 1; }

require_docker() {
  command -v docker >/dev/null || die "Docker no está instalado."
  docker info >/dev/null || die "No se puede acceder al daemon Docker."
}

owned() {
  local kind="$1" name="$2" label
  label="$(docker "$kind" inspect --format "{{index .Labels \"$TM_LABEL_KEY\"}}" "$name")"
  [[ "$label" == "$TM_LABEL_VALUE" ]] || die "$name existe pero no pertenece a este proyecto."
}

owned_container() {
  local label
  label="$(docker container inspect --format "{{index .Config.Labels \"$TM_LABEL_KEY\"}}" "$1")"
  [[ "$label" == "$TM_LABEL_VALUE" ]] || die "$1 no pertenece a este proyecto."
}

diagnostics() {
  local name
  for name in "${TM_CONTAINERS[@]}"; do
    if docker container inspect "$name" >/dev/null 2>&1; then
      printf '\n--- %s ---\n' "$name" >&2
      docker logs --tail 40 "$name" >&2 || true
    fi
  done
}

wait_for() {
  local description="$1"; shift
  local deadline=$((SECONDS + TM_START_TIMEOUT))
  while ((SECONDS < deadline)); do
    if timeout --kill-after=2 15 "$@" >/dev/null 2>&1; then
      printf 'Disponible: %s\n' "$description"
      return 0
    fi
    sleep 2
  done
  printf 'Tiempo agotado: %s\n' "$description" >&2
  return 1
}
