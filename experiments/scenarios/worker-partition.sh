#!/usr/bin/env bash
# Escenario worker-partition (guía 5.3: "pérdida temporal de comunicación y
# reparación"; E3.1 a, E3.2 a). Solo Docker (E3.3): sin root no se aísla la red de
# un proceso local. Con una partida lenta en curso se saca su worker de tm-net,
# se COMPRUEBA que ya no llega al coordinador ni al head (una pausa no es una
# partición), se observa TM_PARTITION_SECONDS (60 s) y se reconecta.
# Hipótesis (08-plan-etapa-5.md): Ray da el nodo por muerto tras su plazo, el
# intento falla o vence nuestro plazo y se reintenta en otro worker, sin derrota;
# lo tardío se descarta. Al reconectar se OBSERVA si Ray readmite el worker o
# hace falta reiniciarlo.
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"
[[ $MODE == docker ]] || { echo "worker-partition solo existe en modo Docker (E3.3)" >&2; exit 2; }
scenario_start worker-partition
observe="${TM_PARTITION_SECONDS:-60}"

reach() {  # contenedor destino puerto → "ok" o "falla"
  if docker exec "$1" python -c 'import socket, sys
socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=3)' "$2" "$3" >/dev/null 2>&1; then
    echo ok
  else
    echo falla
  fi
}
links() {  # estado de los dos enlaces del worker en una línea
  echo "coordinador=$(reach "$1" tm-coordinator 8000) head=$(reach "$1" tm-head 6379)"
}
ip_of() { docker container inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$1"; }

register slow-1 bots/slow.py
register slow-2 bots/slow.py
tournament="scn-worker-partition-$STAMP"
create "$tournament" elimination slow-1 slow-2
event=$(wait_event "$tournament" attempt.dispatched 60)
[[ -n "$event" ]] || { log "No llegó attempt.dispatched"; exit 1; }
victim=$(worker_of_event "$event")
before=$(links "$victim")
ip_before=$(ip_of "$victim")
log "Intento $(field "$event" attempt_id) en $victim ($ip_before); enlaces antes: $before"
sleep 3
docker network disconnect tm-net "$victim"
during=$(links "$victim")
log "Aislado $victim; enlaces durante: $during"
start=$SECONDS
while ((SECONDS - start < observe)); do sleep 5; sample_workers aislado; done
docker network connect tm-net "$victim"
ip_after=$(ip_of "$victim")
log "Reconectado $victim ($ip_after); enlaces después: $(links "$victim")"
# Readmitido = Ray lista un nodo vivo por worker. available en /workers no basta:
# Ray puede dar de baja el nodo después (campaña 30-10-2026: el raylet murió y el
# contenedor siguió running con `ray start` vivo).
readmitted=no
start=$SECONDS
while ((SECONDS - start < 90)); do
  sample_workers reconectado
  if curl -s "$API/api/v1/workers" | grep -q "\"worker_id\":\"$victim\",\"status\":\"available\"" &&
      ray_settled; then
    readmitted=yes; break
  fi
  sleep 5
done
log "Tras reconectar: available=$readmitted en $((SECONDS - start)) s; contenedor $(worker_state "$victim")"
log "Estado final: $(wait_terminal "$tournament" 300)"
dump "$tournament"
run_checks "$tournament" "$victim" "$before" "$during" <<'EOF'
import sys
from checks import Evidence
results, tournament, victim, before, during = sys.argv[1:]
e = Evidence(results, tournament)
attempts = e.attempts()
first = [a for a in attempts if a["generation"] == 1][0]
accepted = [a for a in attempts if a["state"] == "accepted"]
e.check(f"antes del corte el worker llega a coordinador y head ({before})",
        before == "coordinador=ok head=ok")
e.check(f"durante el corte la comunicación falla de verdad ({during})",
        during == "coordinador=falla head=falla")
e.check("el torneo termina completed", e.tournament["state"] == "completed")
e.check("el intento aislado no terminó en derrota de un bot",
        (first["outcome"] or {}).get("kind") != "bot_failure")
print(f"INFO  intento aislado: estado {first['state']}, motivo "
      f"{(first['outcome'] or {}).get('reason')}")
e.check("exactamente un intento aceptado", len(accepted) == 1)
if accepted and accepted[0]["outcome"]["worker_id"] == victim:
    print("INFO  el aceptado se jugó en el worker aislado (terminó antes de que Ray lo diera por muerto)")
late = [d for d in e.of_type("result.discarded") if d["attempt_id"] == first["attempt_id"]]
print(f"INFO  resultados tardíos del intento aislado descartados: {len(late)}")
e.one_result_per_completed_match()
e.done()
EOF
# Siempre se reinicia: aunque Ray lo readmita, puede darlo de baja minutos después
# (el raylet muere y el contenedor sigue running); el siguiente escenario no debe
# heredarlo.
log "Se reinicia $victim para dejar el clúster limpio (readmitido por Ray: $readmitted)"
docker restart "$victim" >/dev/null
worker_restore "$victim"
finish
