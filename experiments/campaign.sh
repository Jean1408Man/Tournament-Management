#!/usr/bin/env bash
# Campaña mínima de 5.3 (E3.5 a): cada escenario con repeticiones y tm verify en
# cada ejecución. Escenarios clave (worker muerto, partición, carga): 5; resto: 3.
# Uso: bash experiments/campaign.sh [docker|local]
#   TM_CAMPAIGN_KEY_REPS / TM_CAMPAIGN_REPS cambian las repeticiones (local: 1 y 1).
# Arranca el clúster (scripts/start-cluster.sh o scripts/local-cluster.sh), lo
# para al final y deja un registro experiments/results/campaign-<modo>-<fecha>.log;
# el resumen lo da: python experiments/summarize.py <ese registro>.
set -uo pipefail
cd "$(dirname -- "${BASH_SOURCE[0]}")/.."
mode="${1:-docker}"
[[ $mode == docker || $mode == local ]] || { echo "Uso: campaign.sh [docker|local]" >&2; exit 2; }
if [[ $mode == local ]]; then key="${TM_CAMPAIGN_KEY_REPS:-1}" rest="${TM_CAMPAIGN_REPS:-1}"
else key="${TM_CAMPAIGN_KEY_REPS:-5}" rest="${TM_CAMPAIGN_REPS:-3}"; fi
mkdir -p experiments/results
log="experiments/results/campaign-$mode-$(date +%Y%m%d-%H%M%S).log"
# Revisión exacta: commit más el diff sin commit de src/ (G1: los commits los hace el usuario).
{ echo "inicio $(date +%Y%m%d-%H%M%S) modo=$mode clave=$key resto=$rest"
  echo "revision $(git rev-parse HEAD 2>/dev/null || echo "${TM_SOURCE_REVISION:-desconocida}")"; } > "$log"
git diff HEAD -- src > "${log%.log}.src.diff" 2>/dev/null || true

cluster() {  # (re)arranca el clúster con las variables dadas
  if [[ $mode == docker ]]; then
    MSYS_NO_PATHCONV=1 bash scripts/stop-cluster.sh >/dev/null 2>&1
    env MSYS_NO_PATHCONV=1 TM_BACKEND=ray "$@" bash scripts/start-cluster.sh >/dev/null 2>&1
  else
    bash scripts/local-cluster.sh stop >/dev/null 2>&1
    env "$@" bash scripts/local-cluster.sh start >/dev/null 2>&1
  fi || { echo "ARRANQUE FALLIDO $*" | tee -a "$log"; exit 1; }
  echo "cluster $* $(date +%T)" | tee -a "$log"
}
run() {  # repeticiones [VAR=valor...] comando...
  local n="$1" i status
  shift
  for ((i = 1; i <= n; i++)); do
    env TM_SCENARIO_MODE="$mode" "$@" >/dev/null 2>&1
    status=$?
    echo "run rep=$i exit=$status $(date +%T) $*" | tee -a "$log"
  done
}
S=experiments/scenarios
SLOW=(--mix round_robin:6x1,elimination:4x2,elimination:2x1 --bot slow --piles "[5,5,5]")
NIM=(--mix round_robin:16x1,elimination:8x2 --bot optimal)

cluster
run "$key" bash "$S/worker-loss.sh"
[[ $mode == docker ]] && run "$key" bash "$S/worker-partition.sh"
run "$key" bash experiments/load/run.sh carga-lenta "${SLOW[@]}"
run "$key" bash experiments/load/run.sh carga-nim "${NIM[@]}"
run "$rest" bash "$S/late-result.sh"
run "$rest" bash "$S/cancelled-tournament.sh"
run "$rest" bash "$S/faulty-bot.sh"
run "$rest" bash "$S/worker-pause.sh"
run "$rest" TM_PAUSE_SECONDS=40 bash "$S/worker-pause.sh"
run "$rest" bash "$S/coordinator-restart.sh"
run "$rest" bash "$S/head-loss.sh"
cluster TM_MAX_ACTIVE_TOURNAMENTS=3
run "$key" bash experiments/load/run.sh saturacion --mix elimination:4x6 --bot slow --piles "[5,5,5]"
if [[ $mode == docker ]]; then MSYS_NO_PATHCONV=1 bash scripts/stop-cluster.sh >/dev/null 2>&1
else bash scripts/local-cluster.sh stop >/dev/null 2>&1; fi
echo "fin $(date +%T)" | tee -a "$log"
