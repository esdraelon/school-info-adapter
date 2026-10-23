"""The CLI shims: real composition path, fixture-backed fetcher seam.

The fetcher is the CLI's external boundary (Google pages), so unit
tests inject fixture text keyed by the production URLs — the same
mapping the M4 system test performs over HTTP.
"""

import io
import json
import shutil
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from school.app import cli
from school.app.config import AppConfig
from school.driver_web import FetchError

REPO_ROOT = Path(__file__).resolve().parents[3]
WEB_FIXTURES = REPO_ROOT / "tests" / "unit" / "driver_web" / "fixtures"
HAC_FIXTURES = REPO_ROOT / "tests" / "unit" / "driver_hac" / "fixtures"

NOW = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)
HEALTH_NOW = NOW + timedelta(minutes=30)

HUB_URL = "https://sites.google.com/leanderisd.org/8th-grade-team-site/home"
MATH_DECK_URL = (
    "https://docs.google.com/presentation/d/e/"
    "2PACX-1vTNlVp2y1IQY8QJE4-7I1zFgDIqVPj1y8gmj3Lilr9jqjlrMWQ-"
    "r58TmlkxdrjdPw0ffzrfbICyG2Dx/pub"
)
TUTORIAL_EMBED_URL = (
    "https://docs.google.com/presentation/d/"
    "1U__2wWHfuv-_61KnL3b05FXNgEL_S5atP3q7XrPipp8/embed"
)

PAGES = {
    HUB_URL: WEB_FIXTURES / "team_site_home.html",
    MATH_DECK_URL: WEB_FIXTURES / "math_agenda_pub.html",
    TUTORIAL_EMBED_URL: WEB_FIXTURES / "tutorial_embed.html",
}


def fixture_fetcher(url: str) -> str:
    target = PAGES.get(url)
    if target is None:
        raise FetchError(f"no fixture page for {url}")
    return target.read_text(encoding="utf-8")


def dead_fetcher(url: str) -> str:
    raise FetchError(f"network is down for {url}")


@pytest.fixture
def cli_config(tmp_path: Path) -> AppConfig:
    cache_path = tmp_path / "cache.json"
    shutil.copyfile(HAC_FIXTURES / "cache_full.json", cache_path)
    registry_path = tmp_path / "deck_registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "decks": [
                    {
                        "subject": "Math 8",
                        "url": MATH_DECK_URL,
                        "template": "weekly-grid-v1",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return AppConfig(
        db_path=str(tmp_path / "school.db"),
        hac_cache_path=str(cache_path),
        token="cli-test-token",
        deck_registry_path=str(registry_path),
    )


def test_scrape_prints_one_line_per_kind_and_exits_zero(
    cli_config: AppConfig,
) -> None:
    out = io.StringIO()
    code = cli.cmd_scrape(
        cli_config, fetcher=fixture_fetcher, clock=lambda: NOW, out=out
    )
    assert code == 0
    lines = out.getvalue().strip().splitlines()
    assert len(lines) == 3
    # Registry order (by kind value): agenda, hac, team_site.
    assert lines[0].startswith("agenda: success")
    assert lines[1].startswith("hac: success")
    assert lines[2].startswith("team_site: success")
    assert "found=" in lines[0]


def test_scrape_exits_one_only_when_every_kind_fails(
    cli_config: AppConfig, tmp_path: Path
) -> None:
    config = replace(
        cli_config, hac_cache_path=str(tmp_path / "absent-cache.json")
    )
    out = io.StringIO()
    code = cli.cmd_scrape(
        config, fetcher=dead_fetcher, clock=lambda: NOW, out=out
    )
    assert code == 1
    lines = out.getvalue().strip().splitlines()
    assert len(lines) == 3
    assert all("failure" in line for line in lines)


def test_health_on_a_fresh_store_exits_one_and_names_never_ran(
    cli_config: AppConfig,
) -> None:
    out = io.StringIO()
    code = cli.cmd_health(
        cli_config, fetcher=fixture_fetcher, clock=lambda: NOW, out=out
    )
    assert code == 1
    payload = json.loads(out.getvalue())
    assert payload["evaluation"]["ok"] is False
    statuses = {
        entry["source_kind"]: entry
        for entry in payload["evaluation"]["entries"]
    }
    assert set(statuses) == {"HAC", "TEAM_SITE", "AGENDA"}
    assert statuses["HAC"]["status"] == "unhealthy"
    assert "never run" in statuses["HAC"]["reason"]
    # The printed source list covers every known source, evaluated or not.
    assert len(payload["sources"]) == 4


def test_health_after_a_successful_scrape_exits_zero(
    cli_config: AppConfig,
) -> None:
    scrape_out = io.StringIO()
    assert (
        cli.cmd_scrape(
            cli_config,
            fetcher=fixture_fetcher,
            clock=lambda: NOW,
            out=scrape_out,
        )
        == 0
    )
    out = io.StringIO()
    code = cli.cmd_health(
        cli_config,
        fetcher=fixture_fetcher,
        clock=lambda: HEALTH_NOW,
        out=out,
    )
    assert code == 0
    payload = json.loads(out.getvalue())
    assert payload["evaluation"]["ok"] is True
    assert all(
        entry["status"] == "ok"
        for entry in payload["evaluation"]["entries"]
    )
    hac = next(s for s in payload["sources"] if s["source_kind"] == "HAC")
    assert hac["last_outcome"] == "SUCCESS"


def test_serve_runs_uvicorn_with_the_composed_app(
    cli_config: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, str, int, bool, str]] = []

    def fake_run(
        app: object,
        *,
        host: str,
        port: int,
        proxy_headers: bool,
        forwarded_allow_ips: str,
    ) -> None:
        calls.append((app, host, port, proxy_headers, forwarded_allow_ips))

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)
    code = cli.cmd_serve(cli_config, fetcher=fixture_fetcher)
    assert code == 0
    [(app, host, port, proxy_headers, forwarded_allow_ips)] = calls
    assert app is not None
    assert host == cli_config.host
    assert port == cli_config.port
    # The gateway deploy depends on proxy-header trust (see cmd_serve).
    assert proxy_headers is True
    assert forwarded_allow_ips == "*"


def test_main_returns_two_and_names_the_missing_variable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = cli.main(["scrape"], env={})
    assert code == 2
    assert "SCHOOL_DB_PATH" in capsys.readouterr().err


def test_main_rejects_an_unknown_subcommand_with_exit_two() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["bogus"], env={})
    assert excinfo.value.code == 2
