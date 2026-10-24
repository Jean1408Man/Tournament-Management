#!/usr/bin/env bash
set -euo pipefail

role="${1:-coordinator}"
if (($#)); then shift; fi
case "$role" in
  coordinator)
    export TM_ROLE=coordinator
    if [[ -n "${TM_DATABASE_URL:-}" ]]; then
      tm migrate --database-url "$TM_DATABASE_URL"
    fi
    exec tm serve --backend "${TM_BACKEND:-local}" --ray-address "${TM_RAY_ADDRESS:-ray://tm-head:10001}" \
      --http-host 0.0.0.0 "$@"
    ;;
  ray-head)
    exec ray start --head --port=6379 --ray-client-server-port=10001 \
      --num-cpus=0 --num-gpus=0 --include-dashboard=false \
      --object-store-memory="${TM_OBJECT_STORE_BYTES:-268435456}" \
      --disable-usage-stats --block "$@"
    ;;
  ray-worker)
    exec ray start --address="${TM_RAY_HEAD_ADDRESS:-tm-head:6379}" \
      --num-cpus="${TM_WORKER_CPUS:-1}" --num-gpus=0 --resources='{"tm_worker": 1}' \
      --object-store-memory="${TM_OBJECT_STORE_BYTES:-268435456}" \
      --disable-usage-stats --block "$@"
    ;;
  *)
    printf 'Rol desconocido: %s. Use coordinator, ray-head o ray-worker.\n' "$role" >&2
    exit 2
    ;;
esac
