"""Diagnóstico remoto de etapa 1; el backend de partidas sigue pendiente."""

import os
import platform
import socket
import time
from uuid import uuid4

from tournament_management import __version__
from tournament_management.cluster.ray_connection import RayConnection
from tournament_management.cluster.ray_topology import validate_topology
from tournament_management.execution.backend import ExecutionError


def identify_worker(nonce: str) -> dict:
    """Se ejecuta en el worker: no recibe una identidad de nodo prefabricada."""
    import ray

    return {
        "node_id": ray.get_runtime_context().get_node_id(),
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "application_version": __version__,
        "python_version": platform.python_version(),
        "ray_version": ray.__version__,
        "nonce": nonce,
    }


def validate_probe_results(
    results: list[dict], expected_ids: set[str], nonce: str, versions: dict[str, str]
) -> None:
    """El conjunto observado debe coincidir exactamente con los workers elegidos."""
    observed = [result.get("node_id") for result in results]
    if len(results) != len(expected_ids) or set(observed) != expected_ids:
        raise ExecutionError("Las tareas no se ejecutaron una vez en cada worker esperado")
    for result in results:
        if result.get("nonce") != nonce:
            raise ExecutionError("Respuesta ajena a esta prueba")
        if not isinstance(result.get("hostname"), str) or not result["hostname"]:
            raise ExecutionError("El worker no informó su hostname")
        if type(result.get("pid")) is not int or result["pid"] <= 0:
            raise ExecutionError("El worker no informó un PID válido")
        if any(result.get(key) != value for key, value in versions.items()):
            raise ExecutionError("Versiones distintas entre coordinador y worker")


def probe_cluster(address: str, expected_workers: int = 2, timeout_seconds: int = 30) -> dict:
    """Envía una tarea fijada a cada worker; no ejecuta torneos ni reintentos."""
    if expected_workers < 1 or timeout_seconds < 1:
        raise ValueError("Número de workers y timeout deben ser positivos")
    connection = RayConnection(address)
    refs = []
    started = time.monotonic()
    try:
        connection.connect()
        import ray
        from ray.util.scheduling_strategies import NodeAffinitySchedulingStrategy

        nodes = connection.snapshot()
        validate_topology(nodes, expected_workers)
        workers = sorted((node for node in nodes if node["worker"]), key=lambda n: n["node_id"])
        nonce = uuid4().hex
        versions = {
            "application_version": __version__,
            "python_version": platform.python_version(),
            "ray_version": ray.__version__,
        }
        task = ray.remote(num_cpus=1, max_retries=0)(identify_worker)
        for worker in workers:
            # soft=False impide ejecutar la tarea en otro nodo si este desaparece.
            ref = task.options(
                scheduling_strategy=NodeAffinitySchedulingStrategy(
                    node_id=worker["node_id"], soft=False
                ),
                resources={"tm_worker": 0.001},
            ).remote(nonce)
            refs.append(ref)
        results = ray.get(refs, timeout=timeout_seconds)
        validate_probe_results(results, {node["node_id"] for node in workers}, nonce, versions)
        return {
            "status": "ok",
            "scope": "remote_execution_per_worker",
            "expected_workers": expected_workers,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "workers": sorted(results, key=lambda result: result["node_id"]),
        }
    except (ExecutionError, ValueError):
        raise
    except Exception as exc:
        raise ExecutionError(
            f"Falló la prueba remota ({type(exc).__name__}); revisa workers, "
            "capacidad y logs del head"
        ) from exc
    finally:
        # También cancelar resultados pendientes si hubo timeout o envío parcial.
        if refs:
            import ray

            for ref in refs:
                try:
                    ray.cancel(ref, force=False)
                except Exception:
                    # La desconexión no debe ocultar el fallo original de la prueba.
                    pass
        connection.close()
