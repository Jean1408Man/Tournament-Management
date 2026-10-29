#!/usr/bin/env bash
# Utilidades comunes de los escenarios de fallo (etapa 4.4).
# Cada escenario guarda su evidencia en experiments/results/<escenario>-<fecha>/
# y termina con código distinto de 0 si alguna comprobación falla.
# Modo actual: Docker (clúster de scripts/start-cluster.sh con TM_BACKEND=ray).
set -euo pipefail

SCENARIO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCENARIO_DIR/../.." && pwd)"
# Rutas relativas a la raíz: valen igual para Python de Windows (Git Bash) y de Linux.
cd "$PROJECT_DIR"
API="${TM_API:-http://127.0.0.1:8000}"
# Sin MSYS_NO_PATHCONV: estos scripts no pasan rutas a docker, y en Git Bash el
# curl de Windows necesita que /dev/null se convierta.
PILES="${TM_SCENARIO_PILES:-[10,10,10]}"

PY=""
for candidate in python python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import json' >/dev/null 2>&1; then
    PY="$candidate"; break
  fi
done
[[ -n "$PY" ]] || { echo "Se requiere Python 3 en el PATH" >&2; exit 2; }

STAMP="$(date +%Y%m%d-%H%M%S)"
RESULTS=""
FAILURES=0

scenario_start() {  # nombre
  RESULTS="experiments/results/$1-$STAMP"
  mkdir -p "$RESULTS"
  : > "$RESULTS/resumen.txt"
  log "Escenario $1 (resultados en experiments/results/$1-$STAMP)"
  curl -sf "$API/ready" >/dev/null || { log "La API no está lista en $API"; exit 2; }
}

log() {
  local line
  line="[$(date +%H:%M:%S)] $*"
  printf '%s\n' "$line" >&2
  if [[ -n "$RESULTS" ]]; then printf '%s\n' "$line" >> "$RESULTS/resumen.txt"; fi
}

# Comprobaciones en Python leídas por stdin (heredoc). El script recibe la carpeta
# de resultados y los argumentos; imprime "OK …"/"FALLO …" y sale con el número
# de fallos, que se suman a los del escenario.
run_checks() {
  local out status
  set +e
  out=$(PYTHONPATH=experiments/scenarios "$PY" - "$RESULTS" "$@")
  status=$?
  set -e
  printf '%s\n' "$out" | tee -a "$RESULTS/resumen.txt"
  FAILURES=$((FAILURES + status))
}

finish() {
  if ((FAILURES)); then
    log "Resultado: $FAILURES comprobación(es) fallida(s)"
    exit 1
  fi
  log "Resultado: todas las comprobaciones correctas"
}

register() {  # id archivo-del-bot
  local status
  status=$(curl -s -o /dev/null -w '%{http_code}' -H 'Content-Type: application/octet-stream' \
    -H "Idempotency-Key: scenario-$1-$(sha256sum "$2" | cut -c1-16)" \
    --data-binary "@$2" \
    "$API/api/v1/competitors?name=$1&filename=bot.py&competitor_id=$1")
  [[ "$status" == 201 ]] || { log "No se pudo registrar $1 (HTTP $status)"; exit 2; }
}

create() {  # id formato participante...
  local id="$1" format="$2" ids status
  shift 2
  ids=$(printf '"%s",' "$@"); ids="${ids%,}"
  status=$(curl -s -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' \
    -H "Idempotency-Key: $id" \
    --data-binary "{\"tournament_id\":\"$id\",\"format\":\"$format\",\"competitor_ids\":[$ids],\"initial_piles\":$PILES,\"first_player\":0,\"seed\":42}" \
    "$API/api/v1/tournaments")
  [[ "$status" == 202 ]] || { log "No se pudo crear $id (HTTP $status)"; exit 2; }
}

state() { curl -s "$API/api/v1/tournaments/$1" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["state"])'; }

wait_terminal() {  # id segundos
  local deadline=$((SECONDS + $2)) s
  while ((SECONDS < deadline)); do
    s=$(state "$1")
    [[ "$s" != running ]] && { echo "$s"; return 0; }
    sleep 1
  done
  echo running
}

wait_event() {  # torneo tipo segundos → imprime el evento (JSON) o nada
  "$PY" - "$API" "$1" "$2" "$3" <<'EOF'
import json, sys, time, urllib.request
api, tournament, kind, seconds = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
deadline = time.monotonic() + seconds
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(f"{api}/api/v1/tournaments/{tournament}/events", timeout=2) as r:
            for raw in r:
                line = raw.decode()
                if line.startswith("data:"):
                    event = json.loads(line[5:])
                    if event["event_type"] == kind:
                        print(json.dumps(event))
                        sys.exit(0)
    except OSError:
        pass  # El flujo de un torneo en curso no se cierra: se vuelve a leer.
    time.sleep(0.2)
EOF
}

field() { "$PY" -c "import json,sys; print(json.loads(sys.argv[1])[sys.argv[2]])" "$1" "$2"; }

worker_of_event() { field "$1" detail | sed 's/^worker=//'; }

sample_workers() {  # etiqueta → añade una muestra con hora a workers.log
  printf '%s %s %s\n' "$(date +%H:%M:%S)" "$1" "$(curl -s --max-time 10 "$API/api/v1/workers")" \
    >> "$RESULTS/workers.log"
}

dump() {  # torneo → guarda torneo, partidas y eventos
  "$PY" - "$API" "$1" "$RESULTS" <<'EOF'
import json, sys, urllib.request
api, tournament, out = sys.argv[1:]
def get(path):
    with urllib.request.urlopen(api + path, timeout=10) as r:
        return json.load(r)
json.dump(get(f"/api/v1/tournaments/{tournament}"), open(f"{out}/{tournament}.tournament.json", "w"), indent=1)
json.dump(get(f"/api/v1/tournaments/{tournament}/matches?limit=100"), open(f"{out}/{tournament}.matches.json", "w"), indent=1)
events = []
try:
    with urllib.request.urlopen(f"{api}/api/v1/tournaments/{tournament}/events", timeout=3) as r:
        for raw in r:
            line = raw.decode()
            if line.startswith("data:"):
                events.append(json.loads(line[5:]))
except OSError:
    pass  # Torneo aún abierto: se guarda lo leído hasta el plazo.
json.dump(events, open(f"{out}/{tournament}.events.json", "w"), indent=1)
EOF
}

# --- Acciones sobre el clúster (modo Docker) ---
worker_kill() { docker kill --signal KILL "$1" >/dev/null; }
worker_pause() { docker pause "$1" >/dev/null; }
worker_unpause() { docker unpause "$1" >/dev/null; }
worker_restore() {  # vuelve a arrancar el worker si no está en marcha y espera a verlo available
  local name="$1" deadline=$((SECONDS + 120))
  [[ "$(docker container inspect --format '{{.State.Running}}' "$name")" == true ]] ||
    docker start "$name" >/dev/null
  while ((SECONDS < deadline)); do
    curl -s "$API/api/v1/workers" | grep -q "\"worker_id\":\"$name\",\"status\":\"available\"" && return 0
    sleep 2
  done
  log "Aviso: $name no volvió a available en 120 s"
}
db_scalar() {  # consulta SQL de solo lectura sobre la base del clúster
  docker exec tm-postgres psql -U tm -d tournament_management -Atc "$1"
}
inject_late() {  # attempt_id → JSON de la herramienta de laboratorio
  docker exec -i tm-coordinator python - "$1" < "$SCENARIO_DIR/inject_late_result.py"
}
