"""M6 wiring: classroom config keys + composition registration.

Both composition paths are pinned: configured (profile dir + account)
registers the driver and adds CLASSROOM_WEB to the health thresholds;
unconfigured leaves the app EXACTLY as M5 built it. The fetcher must
never be called during construction (wiring is I/O-light), and no
browser is launched by building the app — sessions start lazily.
"""

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from school.app.composition import build_app
from school.app.config import AppConfig, config_from_env
from school.domain import Found, SourceKind

REPO_ROOT = Path(__file__).resolve().parents[3]
WEB_FIXTURES = REPO_ROOT / "tests" / "unit" / "driver_web" / "fixtures"
HAC_FIXTURES = REPO_ROOT / "tests" / "unit" / "driver_hac" / "fixtures"
ACCOUNT = "sam@example.test"

BASE_ENV = {
    "SCHOOL_DB_PATH": "/tmp/school.db",
    "SCHOOL_HAC_CACHE_PATH": "/tmp/cache.json",
    "SCHOOL_MCP_TOKEN": "<redacted>",
    "SCHOOL_DECK_REGISTRY": "/tmp/decks.json",
}


def never_fetch(url: str) -> str:
    raise AssertionError(f"fetcher called during build: {url}")


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    cache_path = tmp_path / "cache.json"
    shutil.copyfile(HAC_FIXTURES / "cache_full.json", cache_path)
    registry_path = tmp_path / "deck_registry.json"
    registry_path.write_text(
        json.dumps({"decks": []}), encoding="utf-8"
    )
    return AppConfig(
        db_path=str(tmp_path / "school.db"),
        hac_cache_path=str(cache_path),
        token="<redacted>",
        deck_registry_path=str(registry_path),
    )


class TestClassroomConfig:
    def test_absent_keys_leave_classroom_unconfigured(self) -> None:
        config = config_from_env(BASE_ENV)
        assert config.classroom_profile_dir is None
        assert config.classroom_account is None
        assert config.vault_dir is None
        assert config.classroom_configured is False
        assert config.stale_classroom_hours == 30.0
        assert SourceKind.CLASSROOM_WEB not in config.stale_thresholds

    def test_full_key_set_configures_classroom(self) -> None:
        env = {
            **BASE_ENV,
            "SCHOOL_CLASSROOM_PROFILE_DIR": "/srv/profile",
            "SCHOOL_CLASSROOM_ACCOUNT": ACCOUNT,
            "SCHOOL_VAULT_DIR": "/srv/vault",
            "SCHOOL_STALE_CLASSROOM_HOURS": "48",
        }
        config = config_from_env(env)
        assert config.classroom_profile_dir == "/srv/profile"
        assert config.classroom_account == ACCOUNT
        assert config.vault_dir == "/srv/vault"
        assert config.classroom_configured is True
        assert config.stale_thresholds[SourceKind.CLASSROOM_WEB] == 48.0

    def test_profile_dir_alone_does_not_configure(self) -> None:
        env = {**BASE_ENV, "SCHOOL_CLASSROOM_PROFILE_DIR": "/srv/profile"}
        config = config_from_env(env)
        assert config.classroom_configured is False
        assert SourceKind.CLASSROOM_WEB not in config.stale_thresholds


class TestClassroomComposition:
    def test_unconfigured_build_matches_m5_exactly(
        self, app_config: AppConfig
    ) -> None:
        app = build_app(app_config, fetcher=never_fetch)
        assert app.registry.kinds() == (
            SourceKind.AGENDA,
            SourceKind.HAC,
            SourceKind.TEAM_SITE,
        )

    def test_configured_build_registers_the_classroom_driver(
        self, tmp_path: Path, app_config: AppConfig
    ) -> None:
        config = replace(
            app_config,
            classroom_profile_dir=str(tmp_path / "profile"),
            classroom_account=ACCOUNT,
            vault_dir=str(tmp_path / "vault"),
        )
        (tmp_path / "vault").mkdir()
        app = build_app(config, fetcher=never_fetch)
        assert SourceKind.CLASSROOM_WEB in app.registry.kinds()
        found = app.registry.driver_for(SourceKind.CLASSROOM_WEB)
        assert isinstance(found, Found)
        # Vault was unlocked at build: its key file now exists 0600.
        assert (tmp_path / "vault" / "vault.key").exists()
        assert (tmp_path / "vault" / "vault.json").exists()

    def test_configured_without_vault_still_registers(
        self, tmp_path: Path, app_config: AppConfig
    ) -> None:
        config = replace(
            app_config,
            classroom_profile_dir=str(tmp_path / "profile"),
            classroom_account=ACCOUNT,
        )
        app = build_app(config, fetcher=never_fetch)
        assert SourceKind.CLASSROOM_WEB in app.registry.kinds()
