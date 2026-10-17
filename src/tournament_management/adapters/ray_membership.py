"""Conexión Ray Client y diagnóstico administrativo del despliegue.

La salida de diagnóstico es interna; no constituye el contrato HTTP público.
La traducción completa a WorkerInfo se implementará con el scheduler.
"""

import argparse
import json

from tournament_management.application.ports import ExecutionError


class RayConnection:
    def __init__(self, address: str):
        if not address.startswith("ray://") or address == "ray://":
            raise ValueError("Usa una dirección Ray Client explícita: ray://tm-head:10001")
        self.address = address
        self._ray = None

    def connect(self) -> None:
        import ray

        if self._ray is not None or ray.is_initialized():
            raise ExecutionError("Ya existe una conexión Ray en este proceso")
        try:
            ray.init(address=self.address, logging_level="ERROR", log_to_driver=False)
        except Exception as exc:
            ray.shutdown()
            raise ExecutionError("No se pudo conectar al clúster Ray Client") from exc
        self._ray = ray

    def snapshot(self) -> list[dict]:
        if self._ray is None:
            raise ExecutionError("No hay conexión Ray")
        try:
            return [
                {
                    "node_id": node["NodeID"],
                    "address": node["NodeManagerAddress"],
                    "cpus": node.get("Resources", {}).get("CPU", 0),
                    "worker": node.get("Resources", {}).get("tm_worker", 0) > 0,
                }
                for node in self._ray.nodes()
                if node["Alive"]
            ]
        except Exception as exc:
            raise ExecutionError("No se pudo consultar la membresía Ray") from exc

    def close(self) -> None:
        if self._ray is not None:
            self._ray.shutdown()
            self._ray = None


def validate_topology(nodes: list[dict], expected_workers: int) -> None:
    """Exige workers con CPU y un único nodo de control sin CPU de partidas."""
    workers = [node for node in nodes if node["worker"]]
    control = [node for node in nodes if not node["worker"]]
    if len(workers) != expected_workers or any(node["cpus"] <= 0 for node in workers):
        raise ExecutionError("Número/capacidad de workers inesperado")
    if len(control) != 1 or control[0]["cpus"] != 0:
        raise ExecutionError("Se esperaba un único head con cero CPU de partidas")
    if len({node["node_id"] for node in nodes}) != len(nodes):
        raise ExecutionError("Identidades de nodo duplicadas")


def main() -> None:
    parser = argparse.ArgumentParser(description="Comprobar membresía interna; no ejecuta tareas.")
    parser.add_argument("--address", required=True)
    parser.add_argument("--expected-workers", type=int, default=2)
    args = parser.parse_args()
    if args.expected_workers < 1:
        parser.error("expected-workers debe ser positivo")
    connection = RayConnection(args.address)
    try:
        connection.connect()
        nodes = connection.snapshot()
        validate_topology(nodes, args.expected_workers)
        print(json.dumps({"status": "ok", "nodes": nodes}))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
