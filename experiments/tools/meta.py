"""meta.json de cada ejecución (5.3): revisión, configuración, hardware y comando.

Uso (lo llama scenario_start de lib.sh): python meta.py CARPETA MODO "COMANDO"
Los secretos (token, contraseñas, URL de la base) se guardan enmascarados.
"""

import json
import os
import platform
import subprocess
import sys
import time

SECRET = ("TOKEN", "PASSWORD", "DATABASE_URL")


def run(*command):
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=30)
        return done.stdout.strip() if done.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def settings(lines):
    """Variables TM_* de un entorno (líneas KEY=valor), con secretos enmascarados."""
    found = {}
    for line in lines:
        key, _, value = line.partition("=")
        if key.startswith("TM_"):
            found[key] = "***" if any(s in key for s in SECRET) else value
    return dict(sorted(found.items()))


def docker():
    names = (run("docker", "ps", "--filter", "name=tm-", "--format", "{{.Names}}") or "").split()
    limits = {}
    for name in sorted(names):
        raw = run("docker", "container", "inspect", "--format",
                  "{{.Image}} {{.HostConfig.NanoCpus}} {{.HostConfig.Memory}}", name)
        if raw:
            image, cpus, memory = raw.split()
            limits[name] = {"image": image, "cpus": int(cpus) / 1e9, "memory_bytes": int(memory)}
    info = run("docker", "info", "--format", "{{.ServerVersion}} {{.NCPU}} {{.MemTotal}}")
    version, ncpu, memory = info.split() if info else (None, None, None)
    return {
        "docker_version": version,
        "docker_vm_cpus": int(ncpu) if ncpu else None,
        "docker_vm_memory_bytes": int(memory) if memory else None,
        "containers": limits,
        "coordinator_settings": settings(
            (run("docker", "exec", "tm-coordinator", "env") or "").splitlines()
        ),
    }


def local():
    memory = None
    if os.path.exists("/proc/meminfo"):
        with open("/proc/meminfo", encoding="utf-8") as f:
            memory = next((line.split()[1] for line in f if line.startswith("MemTotal")), None)
    return {
        "kernel": platform.release(),
        "memory_kib": int(memory) if memory else None,
        "settings_from_shell": settings(f"{k}={v}" for k, v in os.environ.items()),
    }


def main():
    out, mode, command = sys.argv[1:4]
    status = run("git", "status", "--porcelain")
    meta = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "command": command,
        "mode": mode,
        # Una copia sin .git (p. ej. en WSL) la declara con TM_SOURCE_REVISION.
        "revision": run("git", "rev-parse", "HEAD") or os.environ.get("TM_SOURCE_REVISION"),
        "uncommitted_files": len(status.splitlines()) if status is not None else None,
        "host": {"platform": platform.platform(), "cpus": os.cpu_count()},
        "seeds": "lib.sh create: seed 42; load.py: seed = índice del torneo en la mezcla",
        "scenario_piles": os.environ.get("TM_SCENARIO_PILES", "[10,10,10]"),
        **(docker() if mode == "docker" else local()),
    }
    with open(os.path.join(out, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)


if __name__ == "__main__":
    main()
