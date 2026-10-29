#!/usr/bin/env bash
# Escenario faulty-bot (P03: "distinguir entre una partida que falla y un worker
# que falla"). Un torneo por cada bot defectuoso contra el óptimo, con workers
# sanos. Se espera bot_failure con victoria del rival en un único intento, sin
# fallos de infraestructura, y que los workers sigan available.
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"
scenario_start faulty-bot
register optimal bots/optimal.py
faulty=(illegal_move invalid_output premature_exit timeout too_much_output stderr_flood)
for bot in "${faulty[@]}"; do
  register "faulty-${bot//_/-}" "bots/faulty/$bot.py"
done
tournaments=()
for bot in "${faulty[@]}"; do
  tournament="scn-faulty-${bot//_/-}-$STAMP"
  create "$tournament" elimination "faulty-${bot//_/-}" optimal
  log "$bot: $(wait_terminal "$tournament" 120)"
  dump "$tournament"
  tournaments+=("$tournament")
done
sample_workers final
workers_json=$(curl -s "$API/api/v1/workers")
run_checks "$workers_json" "${tournaments[@]}" <<'EOF'
import json, sys
from checks import Evidence
results, workers, *tournaments = sys.argv[1:]
failures = 0
for tournament in tournaments:
    e = Evidence(results, tournament)
    name = tournament.split("-", 2)[2].rsplit("-", 2)[0]
    attempts = e.attempts()
    e.check(f"{name}: completed con un único intento",
            e.tournament["state"] == "completed" and len(attempts) == 1)
    outcome = attempts[0]["outcome"] if attempts else {}
    e.check(f"{name}: bot_failure y gana optimal ({outcome.get('reason')})",
            outcome.get("kind") == "bot_failure" and outcome.get("winner_id") == "optimal")
    failures += e.failures
statuses = {w["worker_id"]: w["status"] for w in json.loads(workers)}
ok = bool(statuses) and set(statuses.values()) == {"available"}
print(("OK    " if ok else "FALLO ") + f"los workers siguen available: {statuses}")
sys.exit(failures + (not ok))
EOF
finish
