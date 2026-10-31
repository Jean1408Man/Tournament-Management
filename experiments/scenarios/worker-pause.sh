#!/usr/bin/env bash
# Escenario worker-pause (guía 4.4: "pausar un worker y reanudarlo después de
# expirar su intento"). Congela el worker de una partida lenta durante el plazo
# del intento + 10 s (TM_PAUSE_SECONDS lo cambia). Ray NO da por muerto un worker
# pausado: es nuestro plazo el que vence; se reintenta en otro worker y lo que
# llegue del intento viejo queda descartado.
# Variante corta (TM_PAUSE_SECONDS=40, menos que el plazo): el árbitro detecta que
# estuvo congelado ⇒ infraestructura (referee_stalled) y reintento, nunca derrota;
# si la pausa cae con la jugada ya recibida, no vence ningún plazo y la partida
# termina con normalidad (E3.10: ambos desenlaces valen y se registra cuál).
# No es una partición de red (eso es worker-partition.sh).
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"
scenario_start worker-pause
pause_seconds="${TM_PAUSE_SECONDS:-$(( $(attempt_timeout_seconds) + 10 ))}"
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
log "Estado de $victim tras reanudar: $(worker_state "$victim")"
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
e.check("el intento pausado no terminó en derrota de un bot",
        (first["outcome"] or {}).get("kind") != "bot_failure")
print(f"INFO  intento pausado: estado {first['state']}, motivo "
      f"{(first['outcome'] or {}).get('reason')}")
# E3.10: dos desenlaces válidos. Si la pausa cae mientras el árbitro espera una
# jugada, vence un plazo y el árbitro se culpa (referee_stalled) o vence el
# nuestro: reintento en otro worker. Si cae con la jugada ya recibida, ningún
# plazo vence y la partida termina con normalidad en el worker pausado.
if first["state"] == "accepted":
    print("INFO  desenlace: terminó con normalidad en el worker pausado (ningún plazo venció)")
    e.check("un único intento aceptado: el pausado, sin reintentos",
            len(accepted) == 1 and len(attempts) == 1)
else:
    print("INFO  desenlace: reintento en otro worker")
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
