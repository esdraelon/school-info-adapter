"""Command-line entry: serve / scrape / health over the composition root.

Pattern: thin shim, mirroring ``school.mcp_server.server.main`` — the
CLI parses arguments, builds config from the environment, and
delegates to ``build_app`` plus the runner/health modules. Every
command function takes injected fetcher/clock seams (forwarded to
``build_app``) so tests drive the real CLI path without a network or
a wall clock.

Exit codes: 0 success; 1 operational failure (scrape: every kind
failed; health: report not ok); 2 usage/configuration errors
(argparse, or ConfigError printed to stderr).
"""

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import TextIO

import uvicorn

from school.app.composition import App, build_app
from school.app.config import AppConfig, ConfigError, config_from_env
from school.app.health import evaluate_health
from school.domain import RunOutcome
from school.driver_web import Fetcher
from school.mcp_server import source_health_to_dict


def cmd_serve(
    config: AppConfig,
    *,
    fetcher: Fetcher | None = None,
    clock: Callable[[], datetime] | None = None,
) -> int:
    """Serve the composed MCP app until interrupted."""
    app = build_app(config, fetcher=fetcher, clock=clock)
    uvicorn.run(app.asgi_app, host=config.host, port=config.port)
    return 0


def cmd_scrape(
    config: AppConfig,
    *,
    fetcher: Fetcher | None = None,
    clock: Callable[[], datetime] | None = None,
    out: TextIO | None = None,
) -> int:
    """One scrape pass for the configured student.

    Prints one line per run; exit 1 only when *every* kind failed (or
    none ran) — a single dead source must not page anyone at 3am.
    """
    stream = out if out is not None else sys.stdout
    app = build_app(config, fetcher=fetcher, clock=clock)
    runs = app.scrape_runner.run(app.student)
    for run in runs:
        line = (
            f"{run.source_kind.value}: {run.outcome.value} "
            f"found={run.found} new={run.new} updated={run.updated}"
        )
        if run.message:
            line += f" — {run.message}"
        print(line, file=stream)
    if not runs or all(run.outcome is RunOutcome.FAILURE for run in runs):
        return 1
    return 0


def cmd_health(
    config: AppConfig,
    *,
    fetcher: Fetcher | None = None,
    clock: Callable[[], datetime] | None = None,
    out: TextIO | None = None,
) -> int:
    """Print source health + its evaluation as JSON; exit per report.ok.

    The printed list covers every known source; the evaluation covers
    the kinds with a configured staleness threshold (AppConfig) — the
    sources this deployment is responsible for keeping fresh.
    """
    stream = out if out is not None else sys.stdout
    app: App = build_app(config, fetcher=fetcher, clock=clock)
    now = app.clock()
    health = app.services.source_health(now)
    thresholds = config.stale_thresholds
    report = evaluate_health(
        [entry for entry in health if entry.source_kind in thresholds],
        thresholds,
        now,
    )
    payload = {
        "sources": [source_health_to_dict(entry) for entry in health],
        "evaluation": {
            "ok": report.ok,
            "entries": [
                {
                    "source_kind": entry.source_kind.name,
                    "status": entry.status.value,
                    "reason": entry.reason,
                }
                for entry in report.entries
            ],
        },
    }
    print(json.dumps(payload, indent=2), file=stream)
    return 0 if report.ok else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="school.app",
        description="School Information Adapter: serve the MCP, run a "
        "scrape pass, or report source health.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("serve", help="serve the MCP over HTTP")
    subcommands.add_parser("scrape", help="scrape every source once")
    subcommands.add_parser("health", help="print source health as JSON")
    return parser


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    """Entry point: parse args, build config, dispatch (thin shim)."""
    args = _build_parser().parse_args(argv)
    try:
        config = config_from_env(env if env is not None else os.environ)
        handlers: dict[str, Callable[[AppConfig], int]] = {
            "serve": cmd_serve,
            "scrape": cmd_scrape,
            "health": cmd_health,
        }
        return handlers[args.command](config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
