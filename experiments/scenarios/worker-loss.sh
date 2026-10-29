#!/usr/bin/env bash
# Escenario worker-loss (P03: "si un worker desaparece durante una partida, el
# torneo debe continuar"). Partida lenta, se espera su attempt.dispatched y se
# mata ESE worker. Se espera otro intento (otro attempt_id, generación mayor y
# mismo MatchSpec) en otro worker y exactamente un resultado aceptado.
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"
scenario_start worker-loss
register slow-1 bots/slow.py
register slow-2 bots/slow.py
tournament="scn-worker-loss-$STAMP"
create "$tournament" elimination slow-1 slow-2
event=$(wait_event "$tournament" attempt.dispatched 60)
[[ -n "$event" ]] || { log "No llegó attempt.dispatched"; exit 1; }
victim=$(worker_of_event "$event")
log "Intento $(field "$event" attempt_id) enviado a $victim; se mata en 3 s"
sleep 3
sample_workers antes
worker_kill "$victim"
log "Matado $victim"
for _ in $(seq 1 12); do sleep 5; sample_workers tras-matar; done
log "Estado final: $(wait_terminal "$tournament" 180)"
dump "$tournament"
match_id=$(field "$event" match_id)
specs=$(db_scalar "SELECT count(DISTINCT spec->'match') FROM attempts WHERE match_id = '$match_id'")
run_checks "$tournament" "$victim" "$specs" <<'EOF'
import sys
from checks import Evidence
results, tournament, victim, specs = sys.argv[1:]
e = Evidence(results, tournament)
attempts = e.attempts()
failed = [a for a in attempts if a["state"] == "failed"]
accepted = [a for a in attempts if a["state"] == "accepted"]
e.check("el torneo termina completed", e.tournament["state"] == "completed")
e.check("hay un intento fallido por infraestructura en el worker matado",
        any(a["outcome"]["kind"] == "infrastructure_failure"
            and a["outcome"]["worker_id"] == victim for a in failed))
e.check("exactamente un intento aceptado", len(accepted) == 1)
if accepted and failed:
    winner = accepted[0]
    e.check("el aceptado se jugó en otro worker",
            winner["outcome"]["worker_id"] != victim
            and e.dispatched_worker(winner["attempt_id"]) != victim)
    e.check("el reintento tiene otro attempt_id y generación mayor",
            winner["attempt_id"] != failed[0]["attempt_id"]
            and winner["generation"] > failed[0]["generation"])
e.check("todos los intentos de la partida usan el mismo MatchSpec", specs == "1")
e.one_result_per_completed_match()
e.done()
EOF
worker_restore "$victim"
finish
