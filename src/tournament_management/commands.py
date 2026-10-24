"""Implementaciones de los comandos, separadas del análisis de argumentos."""

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from platform import python_version

from tournament_management import __version__
from tournament_management.config import Settings


def simulate(settings: Settings, args: argparse.Namespace) -> None:
    from tournament_management.application.tournament_service import Competitor, run_tournament
    from tournament_management.referee.runner import run_match

    try:
        with open(args.spec, encoding="utf-8") as stream:
            data = json.load(stream)
        if type(data) is not dict or set(data) != {
            "tournament_id", "format", "competitors", "initial_piles", "first_player", "seed"
        }:
            raise ValueError("Campos del torneo inválidos")
        if type(data["competitors"]) is not list:
            raise ValueError("competitors debe ser una lista")
        bot_directory = (Path(__file__).resolve().parents[2] / "bots").resolve()
        competitors = []
        paths = {}
        for item in data["competitors"]:
            if type(item) is not dict or set(item) != {"id", "bot"}:
                raise ValueError("Cada competidor requiere id y bot")
            if type(item["id"]) is not str or type(item["bot"]) is not str:
                raise ValueError("id y bot deben ser texto")
            bot = (bot_directory / item["bot"]).resolve()
            if not bot.is_relative_to(bot_directory) or not bot.is_file() or bot.suffix != ".py":
                raise ValueError("Bot fuera del directorio local o inexistente")
            paths[item["id"]] = bot
            competitors.append(Competitor(item["id"], hashlib.sha256(bot.read_bytes()).hexdigest()))
        if type(data["initial_piles"]) is not list:
            raise ValueError("initial_piles debe ser una lista")
        if type(data["first_player"]) is not int or data["first_player"] not in (0, 1):
            raise ValueError("first_player debe ser 0 o 1")
        if not data["initial_piles"] or any(
            type(pile) is not int or pile <= 0 for pile in data["initial_piles"]
        ):
            raise ValueError("initial_piles requiere montones positivos")
        if type(data["seed"]) is not int:
            raise ValueError("seed debe ser un entero")

        def execute(attempt, pairing):
            commands = tuple(
                (sys.executable, str(paths[competitor_id]))
                for competitor_id in pairing.competitors
            )
            return run_match(attempt, commands)

        report = run_tournament(
            data["tournament_id"], data["format"], tuple(competitors),
            tuple(data["initial_piles"]), data["first_player"], data["seed"],
            (settings.turn_timeout_ms, settings.match_timeout_ms,
             settings.memory_limit_bytes, settings.output_limit_bytes),
            execute,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "kind": "invalid_spec", "error": str(exc)}))
        raise SystemExit(2) from exc
    print(json.dumps(report, ensure_ascii=False))
    if report["status"] != "completed":
        raise SystemExit(1)


def match(settings: Settings, args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    from tournament_management.domain.messages import AttemptSpec, MatchSpec
    from tournament_management.referee.runner import run_match

    try:
        with open(args.spec, encoding="utf-8") as stream:
            data = json.load(stream)
        match_data = data["match"]
        commands = tuple(tuple(command) for command in data["commands"])
        if len(commands) != 2 or any(not command for command in commands):
            raise ValueError("commands debe contener dos listas no vacías")
        match_spec = MatchSpec.from_dict({"schema_version": 1, **match_data})
        attempt_data = data.get("attempt", {})
        attempt = AttemptSpec(
            attempt_id=attempt_data.get("attempt_id", "a1"),
            generation=attempt_data.get("generation", 1),
            match=match_spec,
            turn_timeout_ms=attempt_data.get("turn_timeout_ms", settings.turn_timeout_ms),
            match_timeout_ms=attempt_data.get("match_timeout_ms", settings.match_timeout_ms),
            memory_limit_bytes=attempt_data.get("memory_limit_bytes", settings.memory_limit_bytes),
            output_limit_bytes=attempt_data.get("output_limit_bytes", settings.output_limit_bytes),
        )
        outcome = run_match(attempt, commands)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "kind": "invalid_spec", "error": str(exc)}))
        raise SystemExit(2) from exc
    print(outcome.to_json())
    if outcome.kind.value != "completed":
        raise SystemExit(1)


def doctor(settings: Settings) -> None:
    from tournament_management.app import check_storage

    configuration = asdict(settings)
    if configuration["database_url"]:
        configuration["database_url"] = "[oculta]"
    report = {
        "configuration": configuration,
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


def probe_cluster(
    settings: Settings, args: argparse.Namespace, parser: argparse.ArgumentParser
) -> None:
    if not settings.ray_address.startswith("ray://") or settings.ray_address == "ray://":
        parser.error("probe-cluster requiere --ray-address ray://host:10001 o TM_RAY_ADDRESS")
    if args.expected_workers < 1 or args.timeout_seconds < 1:
        parser.error("expected-workers y timeout-seconds deben ser positivos")
    from tournament_management.adapters.ray_execution import probe_cluster as execute_probe
    from tournament_management.application.ports import ExecutionError

    try:
        report = execute_probe(settings.ray_address, args.expected_workers, args.timeout_seconds)
    except (ExecutionError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from exc
    print(json.dumps(report, ensure_ascii=False))


def migrate(settings: Settings, parser: argparse.ArgumentParser) -> None:
    if not settings.database_url:
        parser.error("migrate requiere --database-url o TM_DATABASE_URL")
    from tournament_management.adapters.postgres_store import migrate as apply_migrations

    try:
        apply_migrations(settings.database_url)
    except OSError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from exc
    print(json.dumps({"status": "ok", "database": "postgresql"}, ensure_ascii=False))


def serve(settings: Settings, parser: argparse.ArgumentParser) -> None:
    if settings.role != "coordinator":
        parser.error("tm serve requiere el rol coordinator; los roles Ray arrancarán en 1.5")
    from tournament_management.app import create_app
    from tournament_management.observability.logging import configure_logging

    configure_logging(settings.log_level)
    import uvicorn

    uvicorn.run(
        create_app(settings), host=settings.http_host, port=settings.http_port, log_config=None
    )
