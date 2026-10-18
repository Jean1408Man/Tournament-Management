"""Comandos del proyecto; diagnóstico y servidor mínimo en el paso 1.4."""

import argparse
import json
import os
from dataclasses import asdict, fields
from importlib.metadata import version
from platform import python_version

from tournament_management import __version__
from tournament_management.config import Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tm", description="Plataforma distribuida para ejecutar torneos computacionales."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command")
    for name, description in {
        "doctor": "Comprueba configuración, versiones y escritura en el directorio de datos.",
        "serve": "Inicia el servidor mínimo de salud; todavía no ejecuta torneos.",
    }.items():
        command = commands.add_parser(name, help=description, description=description)
        for field in fields(Settings):
            command.add_argument(
                "--" + field.name.replace("_", "-"),
                default=None,
                help=f"Variable TM_{field.name.upper()}",
            )
    probe = commands.add_parser(
        "probe-cluster", help="Ejecuta una tarea de diagnóstico en cada worker Ray."
    )
    probe.add_argument("--ray-address", default=None, help="Dirección ray://host:10001")
    probe.add_argument("--expected-workers", type=int, default=2)
    probe.add_argument("--timeout-seconds", type=int, default=30, help="Plazo de las tareas")
    for name, step in {
        "match": "2.3",
        "simulate": "2.4",
        "verify": "5.2",
    }.items():
        command = commands.add_parser(name, help=f"Pendiente del paso {step}")
        command.set_defaults(pending_step=step)
        if name in {"match", "simulate"}:
            command.add_argument("--spec")
        if name == "verify":
            command.add_argument("--events")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return
    if hasattr(args, "pending_step"):
        parser.error(f"{args.command} no implementado; previsto para el paso {args.pending_step}")
    try:
        settings = Settings.load(vars(args), os.environ)
    except ValueError as exc:
        parser.error(str(exc))
    if args.command == "doctor":
        from tournament_management.app import check_storage

        report = {
            "configuration": asdict(settings),
            "versions": {
                "python": python_version(),
                "application": __version__,
                **{name: version(name) for name in ("ray", "fastapi", "uvicorn")},
            },
            "scope": "configuration_and_storage",
            "cluster_checked": False,
        }
        try:
            check_storage(settings, create=True)
            report["status"] = "ok"
        except OSError as exc:
            report.update(status="error", error=str(exc))
        print(json.dumps(report, ensure_ascii=False))
        if report["status"] == "error":
            raise SystemExit(1)
        return
    if args.command == "probe-cluster":
        if not settings.ray_address.startswith("ray://") or settings.ray_address == "ray://":
            parser.error("probe-cluster requiere --ray-address ray://host:10001 o TM_RAY_ADDRESS")
        if args.expected_workers < 1 or args.timeout_seconds < 1:
            parser.error("expected-workers y timeout-seconds deben ser positivos")
        from tournament_management.adapters.ray_execution import probe_cluster
        from tournament_management.application.ports import ExecutionError

        try:
            report = probe_cluster(
                settings.ray_address, args.expected_workers, args.timeout_seconds
            )
        except (ExecutionError, ValueError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
            raise SystemExit(1) from exc
        print(json.dumps(report, ensure_ascii=False))
        return
    if settings.role != "coordinator":
        parser.error("tm serve requiere el rol coordinator; los roles Ray arrancarán en 1.5")
    from tournament_management.app import create_app
    from tournament_management.observability.logging import configure_logging

    configure_logging(settings.log_level)
    import uvicorn

    uvicorn.run(
        create_app(settings), host=settings.http_host, port=settings.http_port, log_config=None
    )
