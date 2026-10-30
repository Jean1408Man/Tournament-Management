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
        "serve": "Inicia la API v1 y el coordinador de ejecución configurado.",
        "migrate": "Aplica el esquema pendiente de PostgreSQL.",
        "register-bot": "Registra un nuevo archivo Python y sus metadatos durables.",
        "inspect-bot": "Consulta un bot registrado y comprueba sus bytes.",
        "verify-artifact": "Verifica por hash un artefacto local o descargado (TM_ARTIFACT_URL).",
    }.items():
        command = commands.add_parser(name, help=description, description=description)
        for field in fields(Settings):
            command.add_argument(
                "--" + field.name.replace("_", "-"),
                default=None,
                help=f"Variable TM_{field.name.upper()}",
            )
        if name == "register-bot":
            command.add_argument("--file", required=True)
            command.add_argument("--name", required=True)
            command.add_argument("--competitor-id")
            command.add_argument("--idempotency-key", required=True)
            command.add_argument("--protocol", default="nim-jsonl")
            command.add_argument("--protocol-version", type=int, default=1)
            command.add_argument("--runtime", default="python3.12")
        elif name == "inspect-bot":
            command.add_argument("--competitor-id", required=True)
        elif name == "verify-artifact":
            command.add_argument("--artifact-hash", required=True)
    probe = commands.add_parser(
        "probe-cluster", help="Ejecuta una tarea de diagnóstico en cada worker Ray."
    )
    probe.add_argument("--ray-address", default=None, help="Dirección ray://host:10001")
    probe.add_argument("--expected-workers", type=int, default=2)
    probe.add_argument("--timeout-seconds", type=int, default=30, help="Plazo de las tareas")
    match = commands.add_parser("match", help="Ejecuta una partida local desde una especificación.")
    match.add_argument("--spec", required=True)
    simulate = commands.add_parser("simulate", help="Simula un torneo local.")
    simulate.add_argument("--spec", required=True)
    verify = commands.add_parser("verify", help="Pendiente del paso 5.2")
    verify.set_defaults(pending_step="5.2")
    verify.add_argument("--events")
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
    if args.command in {"register-bot", "inspect-bot", "verify-artifact"}:
        command_handlers.registry(settings, args, parser)
        return
    if args.command == "doctor":
        command_handlers.doctor(settings)
        return
    if args.command == "probe-cluster":
        command_handlers.probe_cluster(settings, args, parser)
        return
    if args.command == "migrate":
        command_handlers.migrate(settings, parser)
        return
    if args.command == "match":
        command_handlers.match(settings, args, parser)
        return
    if args.command == "simulate":
        command_handlers.simulate(settings, args)
        return
    command_handlers.serve(settings, parser)
