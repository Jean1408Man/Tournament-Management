#!/usr/bin/env bash
# Escenario head-loss (salida de la etapa 4: "la pérdida del head puede
# interrumpir todo el clúster; documentar su recuperación"). Con una partida
# lenta en curso se mata tm-head. Se observa durante TM_OBSERVE_SECONDS (90 s)
# /ready, /workers, el torneo y el estado de los contenedores; después se aplica
# el procedimiento de recuperación de deploy/OPERATIONS.md. Se exige solo lo
# que es garantía: tras recuperar, el torneo termina con un resultado por partida.
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"
scenario_start head-loss
observe="${TM_OBSERVE_SECONDS:-90}"
register slow-1 bots/slow.py
register slow-2 bots/slow.py
tournament="scn-head-loss-$STAMP"
create "$tournament" elimination slow-1 slow-2
event=$(wait_event "$tournament" attempt.dispatched 60)
[[ -n "$event" ]] || { log "No llegó attempt.dispatched"; exit 1; }
sleep 3
docker kill --signal KILL tm-head >/dev/null
log "Matado tm-head con el intento $(field "$event" attempt_id) en curso"
start=$SECONDS
while ((SECONDS - start < observe)); do
  printf '+%3d s ready=%s torneo=%s contenedores=%s\n' "$((SECONDS - start))" \
    "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$API/ready")" \
    "$(state "$tournament" 2>/dev/null || echo '?')" \
    "$(docker ps -a --filter name=tm- --format '{{.Names}}:{{.State}}' | sort | tr '\n' ' ')" \
    | tee -a "$RESULTS/observacion.log"
  sample_workers sin-head
  sleep 5
done
docker logs --tail 60 tm-coordinator > "$RESULTS/coordinator.log" 2>&1
log "Recuperación: arrancar head, workers y reiniciar coordinador (deploy/OPERATIONS.md)"
docker start tm-head >/dev/null
for name in $(docker ps -a --filter "label=org.tournament-management.project=tournament-management" \
    --format '{{.Names}}' | grep '^tm-worker-'); do
  docker restart "$name" >/dev/null
done
docker restart tm-coordinator >/dev/null
for _ in $(seq 1 90); do
  [[ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$API/ready")" == 200 ]] && break
  sleep 2
done
log "Tras recuperar: /ready $(curl -s -o /dev/null -w '%{http_code}' "$API/ready"), torneo $(wait_terminal "$tournament" 240)"
dump "$tournament"
run_checks "$tournament" <<'EOF'
import sys
from checks import Evidence
results, tournament = sys.argv[1:]
e = Evidence(results, tournament)
e.check("tras la recuperación el torneo termina completed", e.tournament["state"] == "completed")
lost = [x for x in e.of_type("attempt.lost") if x["detail"] == "coordinator_restart"]
print(f"INFO  intentos marcados lost al reiniciar el coordinador: {len(lost)}")
print("INFO  intentos: " + ", ".join(
    f"g{a['generation']}:{a['state']}:{(a['outcome'] or {}).get('reason')}" for a in e.attempts()))
e.one_result_per_completed_match()
e.done()
EOF
finish
