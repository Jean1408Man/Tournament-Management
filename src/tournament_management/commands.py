"""Implementaciones de los comandos, separadas del análisis de argumentos."""

import argparse
import json
from dataclasses import asdict
from importlib.metadata import version
from platform import python_version

from tournament_management import __version__
from tournament_management.config import Settings


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
