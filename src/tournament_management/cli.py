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
    for name, step in {
        "probe-cluster": "1.6",
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
    if settings.role != "coordinator":
        parser.error("tm serve requiere el rol coordinator; los roles Ray arrancarán en 1.5")
    from tournament_management.app import create_app
    from tournament_management.observability.logging import configure_logging

    configure_logging(settings.log_level)
    import uvicorn

    uvicorn.run(
        create_app(settings), host=settings.http_host, port=settings.http_port, log_config=None
    )
