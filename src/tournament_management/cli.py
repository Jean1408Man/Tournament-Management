"""Comandos del proyecto; diagnóstico y servidor mínimo en el paso 1.4."""

import argparse
import os
from dataclasses import fields

from tournament_management import __version__
from tournament_management import commands as command_handlers
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
        "migrate": "Aplica el esquema pendiente de PostgreSQL.",
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
        command_handlers.doctor(settings)
        return
    if args.command == "probe-cluster":
        command_handlers.probe_cluster(settings, args, parser)
        return
    if args.command == "migrate":
        command_handlers.migrate(settings, parser)
        return
    command_handlers.serve(settings, parser)
