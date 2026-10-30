#!/usr/bin/env bash
# Utilidades comunes de los escenarios de fallo (etapa 4.4).
# Cada escenario guarda su evidencia en experiments/results/<escenario>-<fecha>/
# y termina con código distinto de 0 si alguna comprobación falla.
# Modo Docker (scripts/start-cluster.sh con TM_BACKEND=ray) o local
# (scripts/local-cluster.sh start); ver deploy/ARRANQUE.md.
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
  log "Escenario $1 en modo $MODE (resultados en experiments/results/$1-$STAMP)"
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
  verify_evidence
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

dump() {  # torneo → guarda torneo, todas las partidas y el historial completo
  "$PY" - "$API" "$1" "$RESULTS" <<'EOF'
import json, sys, time, urllib.request
api, tournament, out = sys.argv[1:]
base = f"{api}/api/v1/tournaments/{tournament}"
def get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.load(r)
def save(name, data):
    with open(f"{out}/{tournament}.{name}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
save("tournament", get(base))
items, total = [], None
while total is None or len(items) < total:  # la API sirve como mucho 100 por página
    page = get(f"{base}/matches?offset={len(items)}&limit=100")
    items, total = items + page["items"], page["total"]
    if not page["items"]:
        break
save("matches", {"items": items, "total": total})
# El servidor cierra el flujo cuando el torneo es terminal; si se corta antes,
# se reanuda tras el último evento recibido. Lo que falte lo marca tm verify.
events, closed, deadline = [], False, time.monotonic() + 60
while not closed and time.monotonic() < deadline:
    after = events[-1]["sequence"] if events else 0
    try:
        with urllib.request.urlopen(f"{base}/events?after={after}", timeout=10) as r:
            for raw in r:
                line = raw.decode()
                if line.startswith("data:"):
                    event = json.loads(line[5:])
                    if "sequence" not in event:  # stream.error: la base falló a mitad
                        raise ConnectionError(event)
                    events.append(event)
        closed = True
    except OSError:
        time.sleep(0.5)
if not closed:
    print(f"Aviso: el historial de {tournament} no llegó al final en 60 s", file=sys.stderr)
save("events", events)
EOF
}

verify_evidence() {  # tm verify (5.2) sobre todo lo guardado; informe en verify.json
  local status
  set +e
  PYTHONPATH=src PYTHONIOENCODING=utf-8 "$PY" -m tournament_management verify     --evidence "$RESULTS" > "$RESULTS/verify.json"
  status=$?
  set -e
  if ((status)); then
    log "FALLO tm verify: invariantes violados o evidencia incompleta (verify.json)"
    FAILURES=$((FAILURES + 1))
  else
    log "OK    tm verify: sin violaciones de I1–I5 (verify.json)"
  fi
}

# --- Acciones sobre el clúster: Docker o local (scripts/local-cluster.sh) ---
# Se detecta solo: Docker si el contenedor tm-coordinator está en marcha; si no,
# local. TM_SCENARIO_MODE=docker|local lo fuerza.
MODE="${TM_SCENARIO_MODE:-}"
if [[ -z "$MODE" ]]; then
  if [[ "$(docker container inspect --format '{{.State.Running}}' tm-coordinator 2>/dev/null)" == true ]]; then
    MODE=docker
  else
    MODE=local
  fi
fi
[[ "$MODE" == docker || "$MODE" == local ]] || { echo "TM_SCENARIO_MODE debe ser docker o local" >&2; exit 2; }
LOCAL_CLUSTER="bash scripts/local-cluster.sh"
LOCAL_HOME="${TM_LOCAL_HOME:-$HOME/.local/share/tournament-management}"
LOCAL_DB="postgresql:///tournament_management?host=$LOCAL_HOME/run&port=${TM_LOCAL_PG_PORT:-5433}&user=tm"
LOCAL_PY="${TM_VENV:-$PROJECT_DIR/.venv}/bin/python"

local_alive() { [[ -f "$LOCAL_HOME/run/$1.pid" ]] && kill -0 "$(cat "$LOCAL_HOME/run/$1.pid")" 2>/dev/null; }

worker_kill() {
  if [[ $MODE == docker ]]; then docker kill --signal KILL "$1" >/dev/null; else $LOCAL_CLUSTER kill "$1" >/dev/null; fi
}
worker_pause() {
  if [[ $MODE == docker ]]; then docker pause "$1" >/dev/null; else $LOCAL_CLUSTER pause "$1" >/dev/null; fi
}
worker_unpause() {
  if [[ $MODE == docker ]]; then docker unpause "$1" >/dev/null; else $LOCAL_CLUSTER resume "$1" >/dev/null; fi
}
worker_state() {  # en marcha / detenido, para la evidencia
  if [[ $MODE == docker ]]; then
    docker container inspect --format '{{.State.Status}}' "$1"
  elif local_alive "$1"; then echo running; else echo exited; fi
}
worker_restore() {  # vuelve a arrancar el worker si no está en marcha y espera a verlo available
  local name="$1" deadline=$((SECONDS + 120))
  if [[ $MODE == docker ]]; then
    [[ "$(docker container inspect --format '{{.State.Running}}' "$name")" == true ]] ||
      docker start "$name" >/dev/null
  else
    $LOCAL_CLUSTER restore "$name" >/dev/null
  fi
  while ((SECONDS < deadline)); do
    curl -s "$API/api/v1/workers" | grep -q "\"worker_id\":\"$name\",\"status\":\"available\"" && return 0
    sleep 2
  done
  log "Aviso: $name no volvió a available en 120 s"
}
head_kill() {
  if [[ $MODE == docker ]]; then docker kill --signal KILL tm-head >/dev/null; else $LOCAL_CLUSTER kill head >/dev/null; fi
}
cluster_line() {  # estado de cada componente en una línea
  if [[ $MODE == docker ]]; then
    docker ps -a --filter name=tm- --format '{{.Names}}:{{.State}}' | sort | tr '\n' ' '
  else
    local name
    for name in head $(cd "$LOCAL_HOME/run" 2>/dev/null && ls tm-worker-*.pid 2>/dev/null | sed 's/\.pid$//') coordinator; do
      printf '%s:%s ' "$name" "$(local_alive "$name" && echo running || echo exited)"
    done
  fi
}
coordinator_logs() {
  if [[ $MODE == docker ]]; then docker logs --tail 60 tm-coordinator 2>&1; else tail -60 "$LOCAL_HOME/logs/coordinator.log"; fi
}
cluster_recover() {  # procedimiento de recuperación tras perder el head (OPERATIONS.md)
  if [[ $MODE == docker ]]; then
    local name
    docker start tm-head >/dev/null
    for name in $(docker ps -a --filter "label=org.tournament-management.project=tournament-management" \
        --format '{{.Names}}' | grep '^tm-worker-'); do
      docker restart "$name" >/dev/null
    done
    docker restart tm-coordinator >/dev/null
  else
    $LOCAL_CLUSTER recover >/dev/null
  fi
}
db_scalar() {  # consulta SQL de solo lectura sobre la base del clúster
  if [[ $MODE == docker ]]; then
    docker exec tm-postgres psql -U tm -d tournament_management -Atc "$1"
  else
    "$(ls -d /usr/lib/postgresql/*/bin | sort -V | tail -1)/psql" "$LOCAL_DB" -Atc "$1"
  fi
}
attempt_timeout_seconds() {  # plazo del intento con el que corre el coordinador
  local code='import os; from tournament_management.config import Settings; print(Settings.load({}, os.environ).attempt_timeout_ms // 1000)'
  if [[ $MODE == docker ]]; then docker exec tm-coordinator python -c "$code"; else "$LOCAL_PY" -c "$code"; fi
}
inject_late() {  # attempt_id → JSON de la herramienta de laboratorio
  if [[ $MODE == docker ]]; then
    docker exec -i tm-coordinator python - "$1" < "$SCENARIO_DIR/inject_late_result.py"
  else
    TM_DATABASE_URL="$LOCAL_DB" "$LOCAL_PY" - "$1" < "$SCENARIO_DIR/inject_late_result.py"
  fi
}
