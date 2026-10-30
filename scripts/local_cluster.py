"""Nodos Ray del modo sin Docker (etapa 4.4, D4.9 b). Lo usa scripts/local-cluster.sh.

`ray start` se colgó en WSL; aquí se arrancan los nodos como hace
ray.cluster_utils, que ya usan las pruebas. Un proceso por nodo, como un
contenedor por nodo en Docker: matar, pausar o reanudar ese proceso y sus
descendientes equivale a docker kill / pause / unpause.

    python scripts/local_cluster.py head --client-port 10001 --address-file F
    python scripts/local_cluster.py worker --name tm-worker-1 --address-file F

Cada proceso queda vivo hasta recibir SIGTERM o SIGINT, y entonces detiene su nodo.
"""

import argparse
import signal
import socket
import sys
import threading
from pathlib import Path

STORE_BYTES = 100 * 1024 * 1024


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def head(args: argparse.Namespace):
    from ray.cluster_utils import Cluster

    cluster = Cluster(
        initialize_head=True,
        connect=False,
        head_node_args={
            "num_cpus": 0,
            "ray_client_server_port": args.client_port,
            "object_store_memory": STORE_BYTES,
            "include_dashboard": False,
        },
    )
    Path(args.address_file).write_text(cluster.gcs_address)
    return cluster.head_node


def worker(args: argparse.Namespace):
    import ray._private.node
    import ray._private.parameter

    # Mismos parámetros que Cluster.add_node para un nodo que no es head.
    params = ray._private.parameter.RayParams(
        num_cpus=1,
        num_gpus=0,
        resources={"tm_worker": 1},
        node_name=args.name,
        object_store_memory=STORE_BYTES,
        min_worker_port=0,
        max_worker_port=0,
        gcs_address=Path(args.address_file).read_text().strip(),
        include_log_monitor=False,
        node_manager_port=0,
        dashboard_agent_listen_port=free_port(),
    )
    return ray._private.node.Node(params, head=False, shutdown_at_exit=True, spawn_reaper=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="role", required=True)
    h = sub.add_parser("head")
    h.add_argument("--client-port", type=int, default=10001)
    h.add_argument("--address-file", required=True)
    w = sub.add_parser("worker")
    w.add_argument("--name", required=True)
    w.add_argument("--address-file", required=True)
    args = parser.parse_args()

    node = head(args) if args.role == "head" else worker(args)
    print(f"listo {args.role} {getattr(args, 'name', '')}".strip(), flush=True)
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    while not stop.wait(1):
        pass
    node.kill_all_processes(check_alive=False, allow_graceful=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
