"""Panel de observación del clúster (bloque G, E4.15 c).

Sirve ui/index.html en 127.0.0.1 y le da, todo de solo lectura: workers (API
pública), torneos y partidas en ejecución (consulta a PostgreSQL), eventos de un
torneo (SSE público reenviado) y logs (docker logs o los archivos del modo local).
Además lanza una lista cerrada de órdenes (E4.16 b), una a la vez, con su salida
en vivo. Solo biblioteca estándar: vale con el Python de Windows y el de WSL.

Uso: python ui/server.py   (imprime la URL con el token de este arranque)
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
RESULTS = PROJECT / "experiments" / "results"
PAGE = Path(__file__).with_name("index.html")
API = os.environ.get("TM_API", "http://127.0.0.1:8000").rstrip("/")
LOCAL_HOME = Path(os.environ.get(
    "TM_LOCAL_HOME", Path.home() / ".local/share/tournament-management"))
LOCAL_DB = (f"postgresql:///tournament_management?host={LOCAL_HOME}/run"
            f"&port={os.environ.get('TM_LOCAL_PG_PORT', '5433')}&user=tm")
MAX_WORKER = 16
LOG_SOURCE = re.compile(r"^(coordinator|head|tm-worker-\d{1,2})$")
TOURNAMENT_ID = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
RESULTS_LINE = re.compile(r"resultados en (experiments/results/[^\s)]+)")


def detect_mode() -> str:
    """Como lib.sh, salvo que en Windows solo existe Docker (el local va en WSL)."""
    forced = os.environ.get("TM_UI_MODE", "")
    if forced in ("docker", "local"):
        return forced
    if os.name == "nt":
        return "docker"
    try:
        out = subprocess.run(
            ["docker", "container", "inspect", "--format", "{{.State.Running}}",
             "tm-coordinator"], capture_output=True, text=True, timeout=10)
        return "docker" if out.stdout.strip() == "true" else "local"
    except (OSError, subprocess.TimeoutExpired):
        return "local"


def find_bash() -> str:
    """En Windows, el bash de Git; el de System32 es el de WSL."""
    bash = os.environ.get("TM_UI_BASH") or shutil.which("bash") or "bash"
    if os.name == "nt" and "system32" in bash.lower():
        bash = r"C:\Program Files\Git\bin\bash.exe"
    return bash


# --- Órdenes (E4.16 b): lista cerrada, argumentos validados, nunca texto a la shell ---

LOAD_MIXES = {
    "load-lenta": ["carga-lenta", "--mix",
                   "round_robin:6x1,elimination:4x2,elimination:2x1",
                   "--bot", "slow", "--piles", "[5,5,5]"],
    "load-nim": ["carga-nim", "--mix", "round_robin:16x1,elimination:8x2",
                 "--bot", "optimal"],
}
DESCRIPTIONS = {
    "cluster-start": "Levanta PostgreSQL, el head de Ray, 4 workers y el coordinador (1–2 min).",
    "cluster-stop": "Detiene todo; los torneos guardados se conservan.",
    "cluster-status": "Comprueba cada pieza e imprime la configuración del coordinador.",
    "worker-add": "Une un worker nuevo al clúster: más partidas a la vez.",
    "worker-fail": "Mata un worker de golpe; sus partidas se repiten en otro.",
    "worker-recover": "Vuelve a arrancar un worker caído.",
    "scenario:cancelled-tournament": "Cancela con partidas en marcha: nada más se acepta.",
    "scenario:coordinator-restart": "Mata y rearranca el coordinador: no se pierde nada.",
    "scenario:faulty-bot": "Siete bots defectuosos contra el óptimo: pierden ellos, no el worker.",
    "scenario:head-loss": "Mata el head de Ray y aplica la recuperación de OPERATIONS.md.",
    "scenario:late-result": "Entrega un resultado viejo tras un reintento: se descarta.",
    "scenario:worker-loss": "Mata el worker de una partida: se repite en otro, sin derrotas.",
    "scenario:worker-partition": "Corta 60 s la red de un worker: un único resultado aceptado.",
    "scenario:worker-pause": "Congela un worker 100 s: vence el plazo y se reintenta.",
    "scenario:worker-pause-40": "Congela un worker 40 s: el árbitro lo detecta y se reintenta.",
    "load-lenta": "22 partidas largas con bots lentos: los 4 workers ocupados a la vez.",
    "load-nim": "134 partidas rápidas con el bot óptimo: mide al coordinador.",
    "tournament": "Un torneo a tu medida: formato, bots y montones.",
    "verify": "Comprueba las reglas I1–I6 sobre la evidencia de una ejecución.",
}
FORMATS = ("round_robin", "elimination")
MAX_PILES, MAX_PILE = 5, 50


def scenarios(mode: str) -> list[str]:
    names = sorted(p.stem for p in (PROJECT / "experiments/scenarios").glob("*.sh"))
    return [n for n in names if n != "load" and not (mode == "local" and n == "worker-partition")]


def bots() -> list[str]:
    """Bots de bots/ y bots/faulty/ que se pueden elegir para un torneo."""
    root = PROJECT / "bots"
    found = [p.stem for p in root.glob("*.py")]
    found += [f"faulty/{p.stem}" for p in (root / "faulty").glob("*.py")]
    return sorted(found, key=lambda b: (b.startswith("faulty/"), b))


def cluster_workers(mode: str) -> list[dict]:
    """Workers conocidos del clúster, en marcha o caídos."""
    if mode == "docker":
        out = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=tm-worker-", "--format",
             "{{.Names}} {{.State}}"], capture_output=True, text=True, timeout=10)
        pairs = [line.split() for line in out.stdout.splitlines() if line.strip()]
        found = {name: state == "running" for name, state in pairs}
    else:
        # Local: el log queda aunque el worker muera; el pid solo mientras vive.
        found = {p.stem: False for p in (LOCAL_HOME / "logs").glob("tm-worker-*.log")}
        for pid_file in (LOCAL_HOME / "run").glob("tm-worker-*.pid"):
            try:
                os.kill(int(pid_file.read_text()), 0)
                found[pid_file.stem] = True
            except (OSError, ValueError):
                found.setdefault(pid_file.stem, False)
    names = [n for n in found if LOG_SOURCE.match(n) and n.startswith("tm-worker-")]
    return [{"name": n, "up": found[n]}
            for n in sorted(names, key=lambda n: int(n.rsplit("-", 1)[1]))]


def next_worker(workers: list[dict]) -> str | None:
    used = {w["name"] for w in workers}
    return next((f"tm-worker-{n}" for n in range(1, MAX_WORKER + 1)
                 if f"tm-worker-{n}" not in used), None)


def catalog(mode: str) -> list[dict]:
    """Órdenes que ofrece la página: id, grupo, etiqueta, descripción y argumento."""
    items = [
        ("cluster-start", "Clúster", "Arrancar", None),
        ("cluster-stop", "Clúster", "Parar", None),
        ("cluster-status", "Clúster", "Estado", None),
        ("worker-add", "Workers", "Añadir worker", "worker-next"),
        ("worker-fail", "Workers", "Provocar fallo", "worker-up"),
        ("worker-recover", "Workers", "Recuperar worker", "worker-down"),
    ]
    items += [(f"scenario:{n}", "Escenarios", n, None) for n in scenarios(mode)]
    items.append(("scenario:worker-pause-40", "Escenarios", "worker-pause (40 s)", None))
    items += [(k, "Carga", v[0], None) for k, v in LOAD_MIXES.items()]
    items.append(("tournament", "Torneo a medida", "Crear torneo", "tournament"))
    items.append(("verify", "Verificar", "tm verify", "results"))
    return [{"id": i, "group": g, "label": label, "desc": DESCRIPTIONS.get(i, ""),
             "arg": arg} for i, g, label, arg in items]


def results_folders() -> list[str]:
    if not RESULTS.is_dir():
        return []
    found = [p for p in RESULTS.iterdir() if (p / "resumen.txt").is_file()]
    return [p.name for p in sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)]


def result_label(name: str) -> str:
    """worker-loss-20261101-011502 → «worker-loss · 01-11 01:15»."""
    found = re.match(r"^(.*)-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})\d{2}$", name)
    if not found:
        return name
    what, _, month, day, hour, minute = found.groups()
    return f"{what} · {day}-{month} {hour}:{minute}"


def tournament_args(arg: object) -> list[str]:
    """Argumentos de load.sh para un torneo a medida, todos validados."""
    if not isinstance(arg, dict):
        raise ValueError("faltan los datos del torneo")
    size, piles, chosen = arg.get("size"), arg.get("piles"), arg.get("bots")
    if arg.get("format") not in FORMATS:
        raise ValueError("formato desconocido")
    if not isinstance(size, int) or not 2 <= size <= MAX_WORKER:
        raise ValueError(f"el número de bots debe ir de 2 a {MAX_WORKER}")
    if not isinstance(chosen, list) or not 1 <= len(chosen) <= 4 or any(
            b not in bots() for b in chosen):
        raise ValueError("elige de 1 a 4 bots de la lista")
    if not isinstance(piles, list) or not 1 <= len(piles) <= MAX_PILES or any(
            not isinstance(n, int) or not 1 <= n <= MAX_PILE for n in piles):
        raise ValueError(f"de 1 a {MAX_PILES} montones, cada uno de 1 a {MAX_PILE}")
    return ["torneo", "--mix", f"{arg['format']}:{size}x1", "--bot", ",".join(chosen),
            "--piles", json.dumps(piles, separators=(",", ":"))]


def build_command(mode: str, command: str, arg: object,
                  workers: list[dict] | None = None) -> tuple[list[str], dict]:
    """(argv, entorno extra) de una orden de la lista; ValueError si no lo es."""
    bash = find_bash()
    env = {"TM_SCENARIO_MODE": mode}
    name = ""
    if command in ("worker-add", "worker-fail", "worker-recover"):
        workers = cluster_workers(mode) if workers is None else workers
        state = {w["name"]: w["up"] for w in workers}
        name = arg if isinstance(arg, str) and LOG_SOURCE.match(arg) else ""
        valid = {"worker-add": bool(name) and name == next_worker(workers),
                 "worker-fail": state.get(name) is True,
                 "worker-recover": state.get(name) is False}[command]
        if not valid:
            raise ValueError(f"{arg!r} no vale para esta orden con los workers actuales")
    if command == "verify":
        if arg not in results_folders():
            raise ValueError("carpeta de resultados desconocida")
        env.update(PYTHONPATH="src", PYTHONIOENCODING="utf-8")
        return [sys.executable, "-m", "tournament_management", "verify",
                "--evidence", f"experiments/results/{arg}"], env
    if command.startswith("scenario:"):
        scenario = command.split(":", 1)[1]
        if scenario == "worker-pause-40":
            env["TM_PAUSE_SECONDS"] = "40"
            scenario = "worker-pause"
        if scenario not in scenarios(mode):
            raise ValueError("escenario desconocido")
        return [bash, f"experiments/scenarios/{scenario}.sh"], env
    if command in LOAD_MIXES:
        return [bash, "experiments/scenarios/load.sh", *LOAD_MIXES[command]], env
    if command == "tournament":
        return [bash, "experiments/scenarios/load.sh", *tournament_args(arg)], env
    if mode == "docker":
        env["MSYS_NO_PATHCONV"] = "1"  # como campaign.sh: rutas a docker sin convertir
        scripts = {"cluster-start": "start-cluster.sh", "cluster-stop": "stop-cluster.sh",
                   "cluster-status": "cluster-status.sh"}
        if command == "cluster-start":
            env["TM_BACKEND"] = "ray"
        if command in scripts:
            return [bash, f"deploy/docker/{scripts[command]}"], env
        if command == "worker-add":
            return [bash, "deploy/docker/add-worker.sh", name], env
        if command == "worker-fail":
            return [bash, "deploy/docker/fail-worker.sh", name], env
        if command == "worker-recover":
            return ["docker", "start", name], env
    else:
        local = {"cluster-start": "start", "cluster-stop": "stop", "cluster-status": "status"}
        if command in local:
            return [bash, "deploy/local/local-cluster.sh", local[command]], env
        if command == "worker-add":
            return [bash, "deploy/local/local-cluster.sh", "add-worker",
                    name.rsplit("-", 1)[1]], env
        if command == "worker-fail":
            return [bash, "deploy/local/local-cluster.sh", "kill", name], env
        if command == "worker-recover":
            return [bash, "deploy/local/local-cluster.sh", "restore", name], env
    raise ValueError("orden desconocida")


def warning(mode: str) -> str:
    """Avisa cuando el panel no puede hablar con el clúster de su modo."""
    if mode == "docker":
        try:
            ok = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                                capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        if not ok:
            return ("Docker no responde: abre Docker Desktop. Si el clúster es el local de "
                    "WSL, arranca el panel dentro de WSL con TM_UI_MODE=local.")
    elif not (LOCAL_HOME / "run").is_dir():
        return "No hay clúster local: pulsa Arrancar o usa deploy/local/local-cluster.sh start."
    return ""


class Runner:
    """Una ejecución a la vez; guarda su salida para el SSE y para quien llegue tarde."""

    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.run: dict | None = None

    def start(self, label: str, argv: list[str], env: dict) -> dict:
        with self.cond:
            if self.run and self.run["exit_code"] is None:
                raise RuntimeError(f"ya hay una ejecución en marcha: {self.run['label']}")
            process = subprocess.Popen(
                argv, cwd=PROJECT, env={**os.environ, **env}, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            self.run = {"id": secrets.token_hex(4), "label": label, "started": time.time(),
                        "lines": [], "exit_code": None, "results": None}
            threading.Thread(target=self._pump, args=(process, self.run), daemon=True).start()
            return self.status()

    def _pump(self, process: subprocess.Popen, run: dict) -> None:
        for raw in process.stdout:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            with self.cond:
                run["lines"].append(line)
                found = RESULTS_LINE.search(line)
                if found and not run["results"]:
                    run["results"] = found.group(1)
                self.cond.notify_all()
        code = process.wait()
        with self.cond:
            run["exit_code"] = code
            self.cond.notify_all()

    def status(self) -> dict | None:
        if not self.run:
            return None
        return {k: v for k, v in self.run.items() if k != "lines"} | {
            "line_count": len(self.run["lines"])}


# --- Datos de los paneles ---

TOURNAMENTS_SQL = """
SELECT coalesce(json_agg(t ORDER BY t.active DESC, t.created_at DESC), '[]') FROM (
  SELECT tr.tournament_id, tr.state, tr.created_at,
         tr.state IN ('created', 'running') AS active,
         (SELECT count(*) FROM participants p WHERE p.tournament_id = tr.tournament_id)
           AS participants,
         (SELECT coalesce(json_object_agg(state, n), '{}') FROM (
            SELECT state, count(*) AS n FROM matches m
            WHERE m.tournament_id = tr.tournament_id GROUP BY state) s) AS matches,
         (SELECT count(*) FROM attempts a JOIN matches m ON m.match_id = a.match_id
          WHERE m.tournament_id = tr.tournament_id AND a.generation > 1) AS retries
  FROM tournaments tr
  WHERE tr.state IN ('created', 'running') OR tr.tournament_id IN (
    SELECT tournament_id FROM tournaments WHERE state NOT IN ('created', 'running')
    ORDER BY created_at DESC LIMIT 10)
) t
"""

ATTEMPTS_SQL = """
SELECT coalesce(json_agg(r ORDER BY r.seconds DESC), '[]') FROM (
  SELECT a.attempt_id, a.generation, a.state, m.match_id, m.tournament_id,
         m.spec->'competitor_ids' AS competitors, d.worker,
         round(extract(epoch FROM clock_timestamp() - coalesce(d.at, a.created_at)))::int
           AS seconds
  FROM attempts a JOIN matches m ON m.match_id = a.match_id
  LEFT JOIN LATERAL (
    SELECT replace(e.payload->>'detail', 'worker=', '') AS worker, e.occurred_at AS at
    FROM domain_events e
    WHERE e.tournament_id = m.tournament_id AND e.payload->>'attempt_id' = a.attempt_id
      AND e.payload->>'event_type' = 'attempt.dispatched'
    ORDER BY e.sequence DESC LIMIT 1) d ON true
  WHERE a.state IN ('queued', 'running')
) r
"""


def query(mode: str, sql: str) -> object:
    """Consulta de solo lectura (como db_scalar de lib.sh) que devuelve un JSON."""
    readonly = "-c default_transaction_read_only=on"
    if mode == "docker":
        argv = ["docker", "exec", "-e", f"PGOPTIONS={readonly}", "tm-postgres",
                "psql", "-U", "tm", "-d", "tournament_management", "-Atc", sql]
        env = None
    else:
        bins = sorted(Path("/usr/lib/postgresql").glob("*/bin/psql"),
                      key=lambda p: int(p.parts[-3]) if p.parts[-3].isdigit() else 0)
        argv = [str(bins[-1]) if bins else "psql", LOCAL_DB, "-Atc", sql]
        env = {**os.environ, "PGOPTIONS": readonly}
    out = subprocess.run(argv, capture_output=True, timeout=10, env=env)
    if out.returncode:
        raise RuntimeError(out.stderr.decode("utf-8", "replace").strip() or "psql falló")
    return json.loads(out.stdout.decode("utf-8") or "[]")


def api_get(path: str) -> object:
    with urllib.request.urlopen(f"{API}{path}", timeout=5) as response:
        return json.load(response)


def log_sources(mode: str) -> list[str]:
    if mode == "docker":
        out = subprocess.run(["docker", "ps", "-a", "--filter", "name=tm-", "--format",
                              "{{.Names}}"], capture_output=True, text=True, timeout=10)
        names = [n.removeprefix("tm-") if n in ("tm-coordinator", "tm-head") else n
                 for n in out.stdout.split()]
    else:
        names = [p.stem for p in (LOCAL_HOME / "logs").glob("*.log")]
    return sorted(n for n in names if LOG_SOURCE.match(n))


# --- Servidor HTTP ---

class Handler(BaseHTTPRequestHandler):
    server_version = "tm-ui"
    token = ""
    mode = "docker"
    runner = Runner()

    def log_message(self, fmt: str, *args: object) -> None:  # sin ruido por petición
        pass

    # Seguridad: Host propio, token en todas las rutas y Origin propio en las órdenes.
    def own_origins(self) -> set[str]:
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def authorized(self, params: dict) -> bool:
        if self.headers.get("Host", "") not in self.own_origins():
            self.reply(403, {"error": "Host no permitido"})
            return False
        given = self.headers.get("X-TM-UI-Token") or params.get("token", [""])[0]
        if not secrets.compare_digest(given.encode(), self.token.encode()):
            self.reply(403, {"error": "token incorrecto o ausente: usa la URL que imprimió "
                             "este arranque; si hay otro panel en el mismo puerto (uno en "
                             "Windows y otro en WSL), el navegador llega al de Windows"})
            return False
        return True

    def reply(self, status: int, body: object, content_type: str = "application/json") -> None:
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.common_headers()
        self.end_headers()
        self.wfile.write(data)

    def common_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")

    def start_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.common_headers()
        self.end_headers()
        self.wfile.write(b"retry: 2000\n\n")

    def sse(self, data: str, event: str | None = None, event_id: int | None = None) -> None:
        head = (f"event: {event}\n" if event else "") + (
            f"id: {event_id}\n" if event_id is not None else "")
        self.wfile.write(f"{head}data: {data}\n\n".encode())
        self.wfile.flush()

    def do_GET(self) -> None:
        url = urllib.parse.urlsplit(self.path)
        params = urllib.parse.parse_qs(url.query)
        if not self.authorized(params):
            return
        routes = {
            "/": self.page,
            "/api/meta": self.meta,
            "/api/workers": lambda: self.reply(200, api_get("/api/v1/workers")),
            "/api/tournaments": lambda: self.reply(200, query(self.mode, TOURNAMENTS_SQL)),
            "/api/attempts": lambda: self.reply(200, query(self.mode, ATTEMPTS_SQL)),
            "/api/run": lambda: self.reply(200, self.runner.status() or {}),
            "/api/run/stream": self.run_stream,
            "/api/events": lambda: self.events(params.get("tournament", [""])[0]),
            "/api/logs": lambda: self.logs(params.get("source", [""])[0]),
        }
        handler = routes.get(url.path)
        if handler is None:
            self.reply(404, {"error": "ruta desconocida"})
            return
        try:
            handler()
        except (BrokenPipeError, ConnectionResetError):
            pass  # el navegador cerró la conexión
        except ValueError as error:
            self.reply(400, {"error": str(error)})
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            self.reply(502, {"error": str(error)})

    def do_POST(self) -> None:
        url = urllib.parse.urlsplit(self.path)
        if not self.authorized(urllib.parse.parse_qs(url.query)):
            return
        origin = self.headers.get("Origin", "")
        if origin.removeprefix("http://") not in self.own_origins():
            self.reply(403, {"error": "Origin no permitido"})
            return
        if url.path != "/api/run":
            self.reply(404, {"error": "ruta desconocida"})
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            command, arg = body.get("command", ""), body.get("arg")
            argv, env = build_command(self.mode, command, arg)
            if isinstance(arg, dict):
                label = f"torneo {arg.get('format')} de {arg.get('size')} bots"
            else:
                label = command + (f" {arg}" if arg else "")
            self.reply(202, self.runner.start(label, argv, env))
        except (ValueError, AttributeError) as error:
            self.reply(400, {"error": str(error)})
        except RuntimeError as error:
            self.reply(409, {"error": str(error)})

    def meta(self) -> None:
        problem = warning(self.mode)
        workers = [] if problem else cluster_workers(self.mode)
        self.reply(200, {
            "mode": self.mode, "api": API, "warning": problem,
            "commands": catalog(self.mode), "bots": bots(),
            "results": [{"name": n, "label": result_label(n)} for n in results_folders()[:15]],
            "workers": workers, "next_worker": next_worker(workers),
            "logs": [] if problem else log_sources(self.mode)})

    def page(self) -> None:
        self.reply(200, PAGE.read_bytes(), "text/html; charset=utf-8")

    def run_stream(self) -> None:
        """Salida de la ejecución actual desde la línea que ya tenga el navegador."""
        runner = self.runner
        position = int(self.headers.get("Last-Event-ID", "-1")) + 1
        self.start_sse()
        with runner.cond:
            run = runner.run
        if run is None:
            self.sse("{}", "done")
            return
        while True:
            with runner.cond:
                if position >= len(run["lines"]) and run["exit_code"] is None:
                    runner.cond.wait(15)
                lines = run["lines"][position:]
                finished = run["exit_code"] is not None
            for line in lines:
                self.sse(json.dumps(line), event_id=position)
                position += 1
            if finished and position >= len(run["lines"]):
                self.sse(json.dumps({"exit_code": run["exit_code"],
                                     "results": run["results"]}), "done")
                return
            if not lines:
                self.wfile.write(b": ping\n\n")
                self.wfile.flush()

    def events(self, tournament: str) -> None:
        """SSE público del torneo, reenviado sin las líneas event:; al cortarse, el navegador
        vuelve a conectar con Last-Event-ID y la API reanuda (contrato 1.1.0)."""
        if not TOURNAMENT_ID.match(tournament):
            raise ValueError("identificador de torneo no válido")
        request = urllib.request.Request(
            f"{API}/api/v1/tournaments/{urllib.parse.quote(tournament)}/events")
        if self.headers.get("Last-Event-ID"):
            request.add_header("Last-Event-ID", self.headers["Last-Event-ID"])
        try:
            upstream = urllib.request.urlopen(request, timeout=30)
        except urllib.error.HTTPError as error:
            self.reply(error.code, {"error": f"la API respondió {error.code}"})
            return
        self.start_sse()
        try:
            with upstream:
                for raw in upstream:
                    if raw.startswith(b"event:"):
                        continue  # sin nombre de evento: la página los recibe todos en onmessage
                    self.wfile.write(raw)
                    if raw in (b"\n", b"\r\n"):
                        self.wfile.flush()
        except TimeoutError:
            pass

    def logs(self, source: str) -> None:
        if not LOG_SOURCE.match(source):
            raise ValueError("origen de logs no válido")
        self.start_sse()
        if self.mode == "docker":
            name = f"tm-{source}" if source in ("coordinator", "head") else source
            process = subprocess.Popen(
                ["docker", "logs", "-f", "--tail", "200", name], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            try:
                for raw in process.stdout:
                    self.sse(json.dumps(raw.decode("utf-8", "replace").rstrip("\r\n")))
            finally:
                process.kill()
            return
        self.follow(LOCAL_HOME / "logs" / f"{source}.log")

    def follow(self, path: Path) -> None:
        """tail -f de un log local; si el archivo se vacía (worker reiniciado), relee."""
        if not path.is_file():
            self.sse(json.dumps(f"(no existe {path})"))
            return
        with path.open("rb") as log:
            for line in log.readlines()[-200:]:
                self.sse(json.dumps(line.decode("utf-8", "replace").rstrip("\r\n")))
            idle = 0.0
            while True:
                if path.stat().st_size < log.tell():
                    log.seek(0)
                line = log.readline()
                if line:
                    self.sse(json.dumps(line.decode("utf-8", "replace").rstrip("\r\n")))
                    idle = 0.0
                    continue
                time.sleep(0.5)
                idle += 0.5
                if idle >= 15:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    idle = 0.0


def main() -> None:
    port = int(os.environ.get("TM_UI_PORT", "8765"))
    Handler.token = secrets.token_urlsafe(18)
    Handler.mode = detect_mode()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    print(f"Panel en modo {Handler.mode} (API {API})")
    print(f"Abre: http://127.0.0.1:{port}/?token={Handler.token}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
