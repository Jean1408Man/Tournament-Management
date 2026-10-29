#!/usr/bin/env bash
# Escenario late-result (P03: "impedir resultados aceptados duplicados cuando una
# partida reintentada termina informándose más de una vez"). Se pierde el primer
# intento matando su worker; cuando el reintento ya está aceptado, se inyecta un
# resultado deportivo válido del intento viejo con el código real de aceptación.
# Debe quedar result.discarded (stale_attempt) y un único resultado aceptado.
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"
scenario_start late-result
register slow-1 bots/slow.py
register slow-2 bots/slow.py
tournament="scn-late-result-$STAMP"
create "$tournament" elimination slow-1 slow-2
event=$(wait_event "$tournament" attempt.dispatched 60)
[[ -n "$event" ]] || { log "No llegó attempt.dispatched"; exit 1; }
victim=$(worker_of_event "$event")
old_attempt=$(field "$event" attempt_id)
sleep 3
worker_kill "$victim"
log "Matado $victim con el intento $old_attempt en curso"
log "Estado tras el reintento: $(wait_terminal "$tournament" 180)"
injection=$(inject_late "$old_attempt")
log "Inyección del resultado viejo: $injection"
printf '%s\n' "$injection" > "$RESULTS/injection.json"
dump "$tournament"
run_checks "$tournament" "$old_attempt" "$injection" <<'EOF'
import json, sys
from checks import Evidence
results, tournament, old_attempt, injection = sys.argv[1:]
e = Evidence(results, tournament)
e.check("el torneo terminó completed antes de inyectar", e.tournament["state"] == "completed")
e.check("la inyección no fue aceptada", json.loads(injection)["accepted"] is False)
discarded = [d for d in e.of_type("result.discarded") if d["attempt_id"] == old_attempt]
e.check("queda result.discarded del intento viejo por stale_attempt",
        any(d["detail"].endswith("reason=stale_attempt") for d in discarded))
accepted = [a for a in e.attempts() if a["state"] == "accepted"]
e.check("el resultado aceptado sigue siendo el del reintento",
        len(accepted) == 1 and accepted[0]["attempt_id"] != old_attempt)
e.one_result_per_completed_match()
e.done()
EOF
worker_restore "$victim"
finish
