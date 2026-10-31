"""Resumen de una campaña de 5.3: aprobadas, tm verify, mediana y rango.

Uso: python experiments/summarize.py experiments/results/campaign-<modo>-<fecha>.log
Toma las carpetas de experiments/results/ creadas entre el inicio y el fin del
registro y escribe Markdown por la salida estándar. Solo biblioteca estándar.
"""

import json
import re
import statistics
import sys
from pathlib import Path

FOLDER = re.compile(r"^(?P<name>.+)-(?P<stamp>\d{8}-\d{6})$")
CLOCK = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\]")


def spread(values):
    values = [v for v in values if v is not None]
    if not values:
        return "—"
    low, mid, high = min(values), statistics.median(values), max(values)
    return f"{mid:g} [{low:g}–{high:g}]" if len(values) > 1 else f"{mid:g}"


def seconds(line):
    match = CLOCK.match(line)
    return int(match[1]) * 3600 + int(match[2]) * 60 + int(match[3]) if match else None


def run_info(folder):
    text = (folder / "resumen.txt").read_text(encoding="utf-8", errors="replace").splitlines()
    clocks = [s for s in map(seconds, text) if s is not None]
    verify = folder / "verify.json"
    info = {
        "folder": folder.name,
        "passed": any("todas las comprobaciones correctas" in line for line in text),
        "verify": json.loads(verify.read_text(encoding="utf-8"))["ok"] if verify.exists() else None,
        "duration": (clocks[-1] - clocks[0]) % 86400 if len(clocks) > 1 else None,
        "notes": [line.removeprefix("INFO  desenlace: ").strip() for line in text
                  if line.startswith("INFO  desenlace")],
    }
    pause = next((re.search(r"durante (\d+) s", line) for line in text if "Pausado" in line), None)
    info["variant"] = f" ({pause[1]} s)" if pause else ""
    ready = next((re.search(r"listo de nuevo en (-?\d+) s", line) for line in text
                  if "listo de nuevo" in line), None)
    info["restart_s"] = int(ready[1]) if ready else None
    for line in text:
        if "Tras reconectar" in line:
            info["notes"].append("readmitido" if "available=yes" in line else "no readmitido")
        if "INFO  intento aislado" in line or "INFO  intento pausado" in line:
            info["notes"].append(line.split("motivo", 1)[-1].split("(")[0].strip())
    metrics = folder / "metrics.json"
    if metrics.exists():
        info["metrics"] = json.loads(metrics.read_text(encoding="utf-8"))
    return info


def main():
    log = Path(sys.argv[1])
    lines = log.read_text(encoding="utf-8").splitlines()
    start = lines[0].split()[1]
    revision = lines[1].split()[1] if len(lines) > 1 else "?"
    groups: dict[str, list[dict]] = {}
    for folder in sorted(log.parent.iterdir()):
        match = FOLDER.match(folder.name)
        if not folder.is_dir() or not match or match["stamp"] < start:
            continue
        if not (folder / "resumen.txt").exists():
            continue
        info = run_info(folder)
        groups.setdefault(match["name"] + info["variant"], []).append(info)
    print(f"# Campaña {log.stem}\n\nRevisión {revision} (diff de src/ sin commit en "
          f"`{log.stem}.src.diff`). Duraciones en segundos: mediana [mín–máx].\n")
    print("| Escenario | Ejecuciones | Aprobadas | tm verify OK | Duración (s) | Notas |")
    print("|---|---|---|---|---|---|")
    for name, runs in groups.items():
        notes: dict[str, int] = {}
        for run in runs:
            for note in run["notes"]:
                notes[note] = notes.get(note, 0) + 1
        restart = spread([r["restart_s"] for r in runs])
        extra = ([f"coordinador listo en {restart} s"] if restart != "—" else []) + [
            f"{k}: {v}" for k, v in notes.items()]
        print(f"| {name} | {len(runs)} | {sum(r['passed'] for r in runs)} | "
              f"{sum(r['verify'] is True for r in runs)} | "
              f"{spread([r['duration'] for r in runs])} | "
              f"{'; '.join(extra) or '—'} |")
    loads = {k: v for k, v in groups.items() if all("metrics" in r for r in v)}
    if loads:
        print("\n| Carga | Duración (s) | Partidas/s | Intentos simultáneos (media) | Pico | 429 |")
        print("|---|---|---|---|---|---|")
        for name, runs in loads.items():
            m = [r["metrics"] for r in runs]
            print(f"| {name} | {spread([x['run_seconds'] for x in m])} | "
                  f"{spread([x['throughput_per_s'] for x in m])} | "
                  f"{spread([x['mean_running_attempts'] for x in m])} | "
                  f"{spread([x['peak_running_attempts'] for x in m])} | "
                  f"{spread([x['rejections_429'] for x in m])} |")
    print("\nCarpetas: " + ", ".join(r["folder"] for runs in groups.values() for r in runs))


if __name__ == "__main__":
    main()
