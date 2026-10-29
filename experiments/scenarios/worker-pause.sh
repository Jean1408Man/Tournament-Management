#!/usr/bin/env bash
# Escenario worker-pause (guía 4.4: "pausar un worker y reanudarlo después de
# expirar su intento"). Congela ~40 s (TM_PAUSE_SECONDS) el worker de una partida
# lenta. Ray lo da por muerto (~30 s) o vence el plazo del intento: el intento se
# pierde y se reintenta en otro worker. Al reanudar, lo que llegue del intento
# viejo debe quedar descartado. No es una partición de red (eso es 5.3).
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"
scenario_start worker-pause
pause_seconds="${TM_PAUSE_SECONDS:-40}"
register slow-1 bots/slow.py
register slow-2 bots/slow.py
tournament="scn-worker-pause-$STAMP"
create "$tournament" elimination slow-1 slow-2
event=$(wait_event "$tournament" attempt.dispatched 60)
[[ -n "$event" ]] || { log "No llegó attempt.dispatched"; exit 1; }
victim=$(worker_of_event "$event")
log "Intento $(field "$event" attempt_id) enviado a $victim; se pausa en 3 s"
sleep 3
worker_pause "$victim"
log "Pausado $victim durante $pause_seconds s"
for _ in $(seq 1 $((pause_seconds / 5))); do sleep 5; sample_workers pausado; done
worker_unpause "$victim"
log "Reanudado $victim"
for _ in $(seq 1 6); do sleep 5; sample_workers reanudado; done
log "Contenedor $victim tras reanudar: $(docker container inspect --format '{{.State.Status}}' "$victim")"
log "Estado final: $(wait_terminal "$tournament" 180)"
dump "$tournament"
run_checks "$tournament" "$victim" <<'EOF'
import sys
from checks import Evidence
results, tournament, victim = sys.argv[1:]
e = Evidence(results, tournament)
attempts = e.attempts()
first = [a for a in attempts if a["generation"] == 1][0]
accepted = [a for a in attempts if a["state"] == "accepted"]
e.check("el torneo termina completed", e.tournament["state"] == "completed")
e.check("el intento del worker pausado no fue aceptado", first["state"] != "accepted")
print(f"INFO  intento pausado: estado {first['state']}, motivo "
      f"{(first['outcome'] or {}).get('reason')}")
e.check("exactamente un intento aceptado, en otro worker",
        len(accepted) == 1 and accepted[0]["outcome"]["worker_id"] != victim)
late = [d for d in e.of_type("result.discarded") if d["attempt_id"] == first["attempt_id"]]
print(f"INFO  resultados tardíos del intento pausado descartados: {len(late)} "
      + " ".join(d["detail"] for d in late))
e.one_result_per_completed_match()
e.done()
EOF
worker_restore "$victim"
finish
