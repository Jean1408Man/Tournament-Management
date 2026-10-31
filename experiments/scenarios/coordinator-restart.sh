#!/usr/bin/env bash
# Escenario coordinator-restart (guía 5.3: "reinicio del coordinador"; E3.4 a).
# Round-robin de cuatro bots lentos (6 partidas, varias a la vez); con intentos
# en curso se mata el coordinador con SIGKILL y se vuelve a arrancar. Hipótesis:
# lo aceptado antes sigue aceptado; lo que estaba en curso queda attempt.lost
# (coordinator_restart) y se reintenta con otra generación; el cliente se
# reconecta a los eventos desde su última sequence sin ver huecos ni eventos que
# luego desaparezcan (I6 en tm verify).
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"
scenario_start coordinator-restart
down="${TM_DOWN_SECONDS:-10}"
for id in slow-1 slow-2 slow-3 slow-4; do register "$id" bots/slow.py; done
tournament="scn-coordinator-restart-$STAMP"
create "$tournament" round_robin slow-1 slow-2 slow-3 slow-4
event=$(wait_event "$tournament" attempt.dispatched 60)
[[ -n "$event" ]] || { log "No llegó attempt.dispatched"; exit 1; }
sleep 3
attempts_sql="SELECT a.attempt_id FROM attempts a JOIN matches m USING (match_id)
  WHERE m.tournament_id = '$tournament' AND a.state = '%s' ORDER BY a.attempt_id"
coordinator_kill
running=$(db_scalar "$(printf "$attempts_sql" running)" | paste -sd, -)
accepted=$(db_scalar "$(printf "$attempts_sql" accepted)" | paste -sd, -)
log "Matado el coordinador; en curso: ${running:-ninguno}; aceptados: ${accepted:-ninguno}"
sleep "$down"
log "/ready con el coordinador caído: $(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$API/ready" || true)"
log "Coordinador listo de nuevo en $(coordinator_restore) s"
log "Estado final: $(wait_terminal "$tournament" 300)"
dump "$tournament"
run_checks "$tournament" "$running" "$accepted" <<'EOF'
import sys
from checks import Evidence
results, tournament, running, accepted = sys.argv[1:]
running = [a for a in running.split(",") if a]
accepted = [a for a in accepted.split(",") if a]
e = Evidence(results, tournament)
state = {a["attempt_id"]: a["state"] for a in e.attempts()}
lost = {x["attempt_id"]: x["detail"] for x in e.of_type("attempt.lost")}
e.check("había intentos en curso al matar el coordinador", bool(running))
e.check("el torneo termina completed", e.tournament["state"] == "completed")
e.check("lo aceptado antes de morir sigue aceptado",
        all(state.get(a) == "accepted" for a in accepted))
e.check("los intentos en curso al morir quedan perdidos por el reinicio, sin aceptar",
        all(state.get(a) != "accepted" and lost.get(a) == "coordinator_restart" for a in running))
print(f"INFO  intentos: {len(state)} para {len(e.matches)} partidas; "
      f"perdidos por el reinicio: {sum(d == 'coordinator_restart' for d in lost.values())}")
e.one_result_per_completed_match()
e.done()
EOF
finish
