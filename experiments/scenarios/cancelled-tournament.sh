#!/usr/bin/env bash
# Escenario cancelled-tournament (guía 4.4: "repetir con torneo cancelado: no
# puede reabrirse ni avanzar"). Round-robin de tres bots lentos; con la primera
# partida en curso se cancela el torneo, se mata su worker y se inyecta un
# resultado tardío. El torneo debe seguir cancelled, sin intentos ni resultados
# nuevos, y el resultado tardío queda descartado (tournament_not_running).
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"
scenario_start cancelled-tournament
for id in slow-1 slow-2 slow-3; do register "$id" bots/slow.py; done
tournament="scn-cancelled-$STAMP"
create "$tournament" round_robin slow-1 slow-2 slow-3
event=$(wait_event "$tournament" attempt.dispatched 60)
[[ -n "$event" ]] || { log "No llegó attempt.dispatched"; exit 1; }
victim=$(worker_of_event "$event")
attempt=$(field "$event" attempt_id)
sleep 2
log "Cancelar: $(curl -s -X POST "$API/api/v1/tournaments/$tournament/cancel")"
worker_kill "$victim"
log "Matado $victim con el intento $attempt"
injection=$(inject_late "$attempt")
log "Inyección del resultado tardío: $injection"
sleep 20
attempts_after=$(db_scalar "SELECT count(*) FROM attempts a JOIN matches m USING (match_id) WHERE m.tournament_id = '$tournament'")
dump "$tournament"
run_checks "$tournament" "$attempt" "$injection" "$attempts_after" <<'EOF'
import json, sys
from checks import Evidence
results, tournament, attempt, injection, attempts_after = sys.argv[1:]
e = Evidence(results, tournament)
e.check("el torneo sigue cancelled", e.tournament["state"] == "cancelled")
e.check("todas las partidas quedaron cancelled",
        {m["state"] for m in e.matches} == {"cancelled"})
e.check("no se emitieron intentos después de cancelar (solo el que estaba en curso)",
        attempts_after == "1")
e.check("ningún resultado aceptado", not e.of_type("result.accepted"))
e.check("la inyección no fue aceptada", json.loads(injection)["accepted"] is False)
e.check("queda result.discarded por tournament_not_running",
        any(d["attempt_id"] == attempt and d["detail"].endswith("reason=tournament_not_running")
            for d in e.of_type("result.discarded")))
e.done()
EOF
worker_restore "$victim"
finish
