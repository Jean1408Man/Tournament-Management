"""Conexión Ray Client del coordinador; no crea ni busca un clúster local."""

from tournament_management.execution.backend import ExecutionError


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
