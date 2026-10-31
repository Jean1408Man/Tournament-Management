#!/usr/bin/env bash
# Ejecución de carga de 5.1: generador (solo HTTP) más muestreo de conexiones
# PostgreSQL (E1.10) y de memoria/CPU (docker stats, o top en local), y tm verify
# al final (finish de lib.sh). Uso: bash experiments/load/run.sh NOMBRE [opciones de load.py]
# Ejemplo: bash experiments/load/run.sh mezcla --mix round_robin:8x1,elimination:4x3 --bot slow
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../scenarios" && pwd)/lib.sh"
name="${1:?Uso: run.sh NOMBRE [opciones de load.py]}"
shift
scenario_start "load-$name"
log "Opciones: $*"

sample() {  # conexiones abiertas por segundo; CPU y memoria cada ~5 s
  local i=0 f
  while :; do
    printf '%s %s\n' "$(date +%s)" "$(db_scalar "SELECT count(*) FROM pg_stat_activity
      WHERE datname = 'tournament_management'" 2>/dev/null || echo ?)" >> "$RESULTS/pg_connections.log"
    if ((i++ % 5)); then
      :
    elif [[ $MODE == docker ]]; then
      { date +%s; docker stats --no-stream --format '{{.Name}} {{.MemUsage}} {{.CPUPerc}}' \
          $(docker ps --filter name=tm- --format '{{.Names}}'); } >> "$RESULTS/docker_stats.log"
    else
      # Local: proceso principal de cada componente (sin sus hijos: raylet, bots).
      { date +%s
        for f in "$LOCAL_HOME"/run/*.pid; do
          top -b -n 2 -d 0.5 -p "$(cat "$f")" | awk -v n="$(basename "$f" .pid)" \
            '/^top -/ {k++} k == 2 && $1 ~ /^[0-9]+$/ {print n, "cpu=" $9 "%", "rss_kib=" $6}'
        done; } >> "$RESULTS/local_stats.log" 2>/dev/null
    fi
    sleep 1
  done
}
sample &
sampler=$!
trap 'kill "$sampler" 2>/dev/null || true' EXIT

set +e
PYTHONPATH=src PYTHONIOENCODING=utf-8 "$PY" experiments/load/load.py --api "$API" \
  --out "$RESULTS" --prefix "load-$name-$STAMP" "$@" | tee -a "$RESULTS/resumen.txt"
status=${PIPESTATUS[0]}
set -e
kill "$sampler" 2>/dev/null || true
((status == 0)) || { log "FALLO el generador terminó con código $status"; FAILURES=$((FAILURES + 1)); }
peak=$(awk '$2 ~ /^[0-9]+$/ && $2 > m {m = $2} END {print m + 0}' "$RESULTS/pg_connections.log")
log "INFO  conexiones PostgreSQL: máximo muestreado $peak (incluye la del muestreo)"
finish
