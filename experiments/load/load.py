"""Generador de carga de 5.1 (E1.9): solo HTTP y biblioteca estándar.

Registra los bots, crea una mezcla de torneos a la vez (reintentando los 429
según Retry-After), escucha el SSE de cada uno en vivo y guarda en <out>/:
  client.jsonl   lo que recibió el cliente, con su hora (I6 de tm verify)
  <t>.tournament.json, <t>.matches.json, <t>.events.json   evidencia final
  metrics.json   métricas por torneo y globales (E1.8)
  workers.jsonl  muestras de /api/v1/workers cada 0,5 s (intentos simultáneos)
Las horas del servidor (occurred_at) pueden saltar: el reloj de la VM de Docker
se corrige a saltos de ~24 s. Las latencias usan la hora de recepción del
cliente y el orden siempre la sequence.
Las métricas usan PYTHONPATH=src (formatos del servidor, sin dependencias).

Uso: PYTHONPATH=src python experiments/load/load.py --out DIR \\
       --mix round_robin:8x1,elimination:4x3 [--bot slow] [--api URL]
`dump` también lo usa lib.sh: python experiments/load/load.py dump API TORNEO DIR
Los escenarios de fallo (5.3, E3.7) crean con `create API DIR CUERPO` y escuchan
con `follow API DIR TORNEO`; cada torneo deja client-<torneo>.jsonl.
`metrics DIR` recalcula metrics.json de una carpeta ya guardada.
"""

import argparse
import hashlib
import json
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def request(method, url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, dict(response.headers), json.load(response)
    except urllib.error.HTTPError as error:
        with error:
            return error.code, dict(error.headers), json.loads(error.read() or b"null")


def dump(api, tournament, out):
    """Torneo, todas las partidas y el historial completo, como en lib.sh."""
    base = f"{api}/api/v1/tournaments/{tournament}"

    def save(name, data):
        with open(f"{out}/{tournament}.{name}.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)

    save("tournament", request("GET", base)[2])
    items, total = [], None
    while total is None or len(items) < total:  # la API sirve como mucho 100 por página
        page = request("GET", f"{base}/matches?offset={len(items)}&limit=100")[2]
        items, total = items + page["items"], page["total"]
        if not page["items"]:
            break
    save("matches", {"items": items, "total": total})
    events, closed = listen(base, deadline=time.monotonic() + 60)
    if not closed:
        print(f"Aviso: el historial de {tournament} no llegó al final", file=sys.stderr)
    save("events", events)


def listen(base, deadline, on_event=None):
    """Lee el SSE hasta que el servidor lo cierra (torneo terminal); reanuda
    tras el último evento si se corta. Devuelve (eventos, cerrado)."""
    events = []
    while time.monotonic() < deadline:
        after = events[-1]["sequence"] if events else 0
        try:
            with urllib.request.urlopen(f"{base}/events?after={after}", timeout=20) as r:
                for raw in r:
                    line = raw.decode()
                    if line.startswith("data:"):
                        event = json.loads(line[5:])
                        if "sequence" not in event:  # stream.error: la base falló a mitad
                            raise ConnectionError(event)
                        events.append(event)
                        if on_event:
                            on_event(event)
            return events, True
        except OSError:
            time.sleep(0.5)
    return events, False


class Client:
    def __init__(self, api, out, log_name="client.jsonl"):
        self.api, self.out = api, Path(out)
        self.lock = threading.Lock()
        self.log = open(self.out / log_name, "a", encoding="utf-8")

    def note(self, kind, tournament_id, **fields):
        line = json.dumps(
            {"at": time.time(), "kind": kind, "tournament_id": tournament_id, **fields}
        )
        with self.lock:
            self.log.write(line + "\n")
            self.log.flush()

    def register(self, competitor_id, source):
        digest = hashlib.sha256(source).hexdigest()[:16]
        query = urllib.parse.urlencode(
            {"name": competitor_id, "filename": "bot.py", "competitor_id": competitor_id}
        )
        status, _, body = request(
            "POST",
            f"{self.api}/api/v1/competitors?{query}",
            data=source,
            headers={
                "Content-Type": "application/octet-stream",
                "Idempotency-Key": f"load-{competitor_id}-{digest}",
            },
        )
        if status not in (200, 201):
            raise SystemExit(f"No se pudo registrar {competitor_id}: HTTP {status} {body}")

    def post(self, tournament_id, body):
        payload = json.dumps({**body, "tournament_id": tournament_id}).encode()
        return request(
            "POST",
            f"{self.api}/api/v1/tournaments",
            data=payload,
            headers={"Content-Type": "application/json", "Idempotency-Key": tournament_id},
        )

    def follow(self, tournament_id, deadline):
        base = f"{self.api}/api/v1/tournaments/{tournament_id}"
        listen(base, deadline, lambda e: self.note("event", tournament_id, event=e))

    def run(self, tournament_id, body, deadline):
        """Crea (respetando 429), escucha en vivo hasta el final y guarda la evidencia."""
        while True:
            status, headers, answer = self.post(tournament_id, body)
            if status == 202:
                self.note("created", tournament_id, status=status)
                break
            if status != 429 or time.monotonic() > deadline:
                self.note("failed", tournament_id, status=status, body=answer)
                return
            wait = int(headers.get("Retry-After", "5"))
            self.note("rejected", tournament_id, status=status, retry_after=wait)
            time.sleep(wait)
        self.follow(tournament_id, deadline)
        dump(self.api, tournament_id, self.out)


def parse_mix(text):
    """round_robin:8x1,elimination:4x3 → [(formato, participantes), ...]."""
    mix = []
    for item in text.split(","):
        name, _, spec = item.partition(":")
        size, _, count = spec.partition("x")
        if name not in {"round_robin", "elimination"} or not 2 <= int(size) <= 64:
            raise SystemExit(f"Mezcla inválida: {item}")
        mix += [(name, int(size))] * int(count or 1)
    return mix


def percentile(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, round(q * (len(values) - 1)))] if values else None


def summary(values):
    if not values:
        return None
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 3),
        "p50": round(percentile(values, 0.5), 3),
        "p95": round(percentile(values, 0.95), 3),
    }


def mark_ready(replay, ready_since, tournament_id, at):
    """Anota cuándo quedó lista cada partida que el formato ofrece ahora."""
    from tournament_management.tournaments.model import match_identifier

    for pairing in replay.pending():
        ready_since.setdefault(match_identifier(tournament_id, pairing.match_id), at)


def sample_workers(api, out, stop):
    """Intentos en marcha por worker cada 0,5 s, hasta que se pida parar."""
    with open(Path(out) / "workers.jsonl", "a", encoding="utf-8") as log:
        while not stop.wait(0.5):
            status, _, workers = request("GET", f"{api}/api/v1/workers")
            if status == 200:
                running = {w["worker_id"]: w["running_attempts"] for w in workers}
                log.write(json.dumps({"at": time.time(), "running": running}) + "\n")
                log.flush()


def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def metrics(out):
    """Métricas por torneo; horas de recepción del cliente si las hay (ver arriba)."""
    from tournament_management.tournaments.model import match_identifier, new_format

    out = Path(out)
    client = read_jsonl(out / "client.jsonl")
    received = {r["event"]["event_id"]: r["at"] for r in client if r["kind"] == "event"}

    def stamp(event):
        server = datetime.fromisoformat(event["occurred_at"]).timestamp()
        return received.get(event["event_id"], server)

    per_tournament, accepted_at, per_worker = {}, [], {}
    for path in sorted(out.glob("*.tournament.json")):
        tid = path.name.removesuffix(".tournament.json")
        tournament = json.loads(path.read_text(encoding="utf-8"))
        matches = json.loads((out / f"{tid}.matches.json").read_text(encoding="utf-8"))["items"]
        events = sorted(
            json.loads((out / f"{tid}.events.json").read_text(encoding="utf-8")),
            key=lambda e: e["sequence"],
        )
        replay = new_format(tournament["format"], tuple(tournament["competitor_ids"]))
        winners = {m["match_id"]: m["winner_id"] for m in matches}
        ready_since, issued, waits, service = {}, {}, [], []

        for event in events:
            at, kind = stamp(event), event["event_type"]
            if kind == "tournament.created":
                mark_ready(replay, ready_since, tid, at)
            elif kind == "attempt.issued":
                issued[event["attempt_id"]] = at
                if event["match_id"] in ready_since:
                    waits.append(at - ready_since.pop(event["match_id"]))
            elif kind == "attempt.dispatched":
                worker = event["detail"].removeprefix("worker=")
                per_worker[worker] = per_worker.get(worker, 0) + 1
            if kind == "result.accepted":
                accepted_at.append(at)
                service.append(at - issued[event["attempt_id"]])
                pairing = next(
                    p
                    for p in replay.pending()
                    if match_identifier(tid, p.match_id) == event["match_id"]
                )
                replay.record(pairing.match_id, winners[event["match_id"]])
                mark_ready(replay, ready_since, tid, at)
        started = sum(1 for m in matches if m["attempts"])
        attempts = sum(len(m["attempts"]) for m in matches)
        created = stamp(events[0])
        last = max(
            (stamp(e) for e in events if e["event_type"] == "result.accepted"), default=created
        )
        per_tournament[tid] = {
            "format": tournament["format"],
            "participants": len(tournament["competitor_ids"]),
            "state": tournament["state"],
            "matches": len(matches),
            "completed": tournament["statistics"]["completed"],
            "attempts": attempts,
            "retries": attempts - started,
            "duration_s": round(last - created, 3),
            "ready_to_issued_s": summary(waits),
            "issued_to_accepted_s": summary(service),
        }
    samples = [sum(s["running"].values()) for s in read_jsonl(out / "workers.jsonl")]
    created = [r["at"] for r in client if r["kind"] == "created"]
    window = max(accepted_at) - min(created) if accepted_at and created else 0
    return {
        "run_seconds": round(window, 3),
        "accepted_results": len(accepted_at),
        "throughput_per_s": round(len(accepted_at) / window, 3) if window else None,
        "peak_running_attempts": max(samples, default=None),
        "mean_running_attempts": round(statistics.fmean(samples), 2) if samples else None,
        "attempts_per_worker": dict(sorted(per_worker.items())),
        "rejections_429": sum(1 for r in client if r["kind"] == "rejected"),
        "tournaments": per_tournament,
    }


def main():
    if sys.argv[1:2] == ["dump"]:
        dump(*sys.argv[2:5])
        return
    if sys.argv[1:2] == ["create"]:  # create API DIR CUERPO_JSON → imprime el código HTTP
        api, out, body = sys.argv[2], sys.argv[3], json.loads(sys.argv[4])
        tournament_id = body["tournament_id"]
        client = Client(api, out, f"client-{tournament_id}.jsonl")
        status, _, answer = client.post(tournament_id, body)
        if status == 202:
            client.note("created", tournament_id, status=status)
        else:
            client.note("failed", tournament_id, status=status, body=answer)
        print(status)
        return
    if sys.argv[1:2] == ["follow"]:  # follow API DIR TORNEO: escucha hasta que sea terminal
        api, out, tournament_id = sys.argv[2:5]
        Client(api, out, f"client-{tournament_id}.jsonl").follow(
            tournament_id, time.monotonic() + 1800
        )
        return
    if sys.argv[1:2] == ["metrics"]:
        result = metrics(sys.argv[2])
        Path(sys.argv[2], "metrics.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
        print(json.dumps({k: v for k, v in result.items() if k != "tournaments"}))
        return
    parser = argparse.ArgumentParser(description="Generador de carga de la etapa 5.1")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--out", required=True)
    parser.add_argument("--mix", default="round_robin:6x1,elimination:4x3")
    parser.add_argument("--bot", default="slow", help="bots/<bot>.py para todos")
    parser.add_argument("--piles", default="[10,10,10]")
    parser.add_argument("--prefix", default=time.strftime("load-%Y%m%d-%H%M%S"))
    parser.add_argument("--timeout", type=int, default=1800, help="segundos máximos")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    mix = parse_mix(args.mix)
    client = Client(args.api, out)
    source = (ROOT / "bots" / f"{args.bot}.py").read_bytes()
    competitors = [f"{args.bot}-{i:02d}" for i in range(1, max(n for _, n in mix) + 1)]
    for competitor in competitors:
        client.register(competitor, source)
    deadline = time.monotonic() + args.timeout
    stop = threading.Event()
    sampler = threading.Thread(target=sample_workers, args=(args.api, out, stop), daemon=True)
    sampler.start()
    threads = []
    for index, (format_name, size) in enumerate(mix, 1):
        body = {
            "format": format_name,
            "competitor_ids": competitors[:size],
            "initial_piles": json.loads(args.piles),
            "first_player": 0,
            "seed": index,
        }
        thread = threading.Thread(
            target=client.run, args=(f"{args.prefix}-{index:02d}", body, deadline), daemon=True
        )
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join(max(0, deadline - time.monotonic()))
    stop.set()
    sampler.join()
    result = metrics(out)
    (out / "metrics.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "tournaments"}))


if __name__ == "__main__":
    main()
