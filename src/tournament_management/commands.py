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
    from tournament_management.execution.backends.local import LocalExecution
    from tournament_management.game.referee import run_match
    from tournament_management.simulation.result_service import ResultService
    from tournament_management.simulation.scheduler import Scheduler
    from tournament_management.simulation.tournament_service import Competitor, run_tournament

    try:
        with open(args.spec, encoding="utf-8") as stream:
            data = json.load(stream)
        if type(data) is not dict or set(data) != {
            "tournament_id",
            "format",
            "competitors",
            "initial_piles",
            "first_player",
            "seed",
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

        def execute_attempt(attempt, cancel):
            commands = tuple(
                (sys.executable, str(paths[competitor_id]))
                for competitor_id in attempt.match.competitor_ids
            )
            return run_match(attempt, commands, cancel=cancel)

        with LocalExecution(execute_attempt) as backend:
            scheduler = Scheduler(
                backend,
                ResultService(),
                max_retries=settings.max_retries,
                attempt_timeout_ms=settings.attempt_timeout_ms,
            )
            report = run_tournament(
                data["tournament_id"],
                data["format"],
                tuple(competitors),
                tuple(data["initial_piles"]),
                data["first_player"],
                data["seed"],
                (
                    settings.turn_timeout_ms,
                    settings.match_timeout_ms,
                    settings.memory_limit_bytes,
                    settings.output_limit_bytes,
                ),
                lambda attempt, pairing: scheduler.run_match(attempt),
            )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "kind": "invalid_spec", "error": str(exc)}))
        raise SystemExit(2) from exc
    print(json.dumps(report, ensure_ascii=False))
    if report["status"] != "completed":
        raise SystemExit(1)


def match(settings: Settings, args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    from tournament_management.game.referee import run_match
    from tournament_management.shared.messages import AttemptSpec, MatchSpec

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
    for secret in ("database_url", "internal_token"):
        if configuration[secret]:
            configuration[secret] = "[oculta]"
    report = {
        "configuration": configuration,
        "versions": {
            "python": python_version(),
            "application": __version__,
            **{name: version(name) for name in ("ray", "fastapi", "uvicorn")},
        },
        "scope": "configuration_and_storage",
        "cluster_checked": False,
        "warnings": settings.timing_warnings(),
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
    from tournament_management.cluster.ray_probe import probe_cluster as execute_probe
    from tournament_management.execution.backend import ExecutionError

    try:
        report = execute_probe(settings.ray_address, args.expected_workers, args.timeout_seconds)
    except (ExecutionError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from exc
    print(json.dumps(report, ensure_ascii=False))


def migrate(settings: Settings, parser: argparse.ArgumentParser) -> None:
    if not settings.database_url:
        parser.error("migrate requiere --database-url o TM_DATABASE_URL")
    from tournament_management.database.unit_of_work import migrate as apply_migrations

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
    from tournament_management.shared.logging import configure_logging

    configure_logging(settings.log_level)
    import uvicorn

    uvicorn.run(
        create_app(settings), host=settings.http_host, port=settings.http_port, log_config=None,
        timeout_graceful_shutdown=5,
    )


def registry(settings: Settings, args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Herramientas de 3.2; el registro HTTP de archivos se añadirá en 3.3."""
    from tournament_management.competitors.artifact_store import FileArtifactStore
    from tournament_management.competitors.service import CompetitorService
    from tournament_management.database.unit_of_work import PostgresUnitOfWork

    if args.command != "verify-artifact" and not settings.database_url:
        parser.error("Este comando requiere --database-url o TM_DATABASE_URL")
    store = FileArtifactStore(
        settings.data_path / "artifacts",
        max_bytes=settings.artifact_max_bytes,
    )
    service = CompetitorService(
        lambda: PostgresUnitOfWork(settings.database_url),
        store,
        max_bytes=settings.artifact_max_bytes,
    )
    try:
        if args.command == "verify-artifact":
            reader = store
            if settings.artifact_url:
                # En un worker (4.2): descarga del coordinador, no volumen local.
                from tournament_management.competitors.remote_store import RemoteArtifactStore

                reader = RemoteArtifactStore(
                    settings.artifact_url, settings.internal_token,
                    max_bytes=settings.artifact_max_bytes,
                    timeout_ms=settings.artifact_download_timeout_ms,
                )
            content = reader.get(args.artifact_hash)
            report = {
                "artifact_hash": args.artifact_hash,
                "size_bytes": len(content),
                "source": "download" if settings.artifact_url else "local",
            }
        elif args.command == "inspect-bot":
            report = json.loads(service.get(args.competitor_id).to_json())
        else:
            with Path(args.file).open("rb") as source:
                registered = service.register(
                    source,
                    filename=Path(args.file).name,
                    name=args.name,
                    competitor_id=args.competitor_id,
                    idempotency_key=args.idempotency_key,
                    protocol=args.protocol,
                    protocol_version=args.protocol_version,
                    runtime=args.runtime,
                )
            report = json.loads(registered.to_json())
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from exc
    print(json.dumps({"status": "ok", **report}, ensure_ascii=False))
