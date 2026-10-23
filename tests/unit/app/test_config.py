"""config_from_env: every branch — defaults, overrides, and each
ConfigError naming the offending variable."""

import pytest

from school.app.config import ConfigError, config_from_env
from school.domain import SourceKind

BASE_ENV = {
    "SCHOOL_DB_PATH": "/tmp/school.db",
    "SCHOOL_HAC_CACHE_PATH": "/cache/sam_classwork_cache.json",
    "SCHOOL_MCP_TOKEN": "test-token",
    "SCHOOL_DECK_REGISTRY": "/cfg/deck_registry.json",
}


def test_minimal_env_uses_production_defaults() -> None:
    config = config_from_env(BASE_ENV)
    assert config.db_path == "/tmp/school.db"
    assert config.hac_cache_path == "/cache/sam_classwork_cache.json"
    assert config.token == "test-token"
    assert config.deck_registry_path == "/cfg/deck_registry.json"
    assert config.student_id == "sam"
    assert config.student_name == "Sam"
    assert config.team_site_url == (
        "https://sites.google.com/leanderisd.org/8th-grade-team-site/home"
    )
    assert config.host == "127.0.0.1"
    assert config.port == 8765
    assert config.stale_hac_hours == 30.0
    assert config.stale_web_hours == 192.0


def test_full_env_overrides_every_default() -> None:
    env = dict(
        BASE_ENV,
        SCHOOL_STUDENT_ID="owen",
        SCHOOL_STUDENT_NAME="Owen",
        SCHOOL_TEAM_SITE_URL="https://example.test/hub",
        SCHOOL_HOST="0.0.0.0",
        SCHOOL_PORT="9999",
        SCHOOL_STALE_HAC_HOURS="12.5",
        SCHOOL_STALE_WEB_HOURS="48",
    )
    config = config_from_env(env)
    assert config.student_id == "owen"
    assert config.student_name == "Owen"
    assert config.team_site_url == "https://example.test/hub"
    assert config.host == "0.0.0.0"
    assert config.port == 9999
    assert config.stale_hac_hours == 12.5
    assert config.stale_web_hours == 48.0


def test_blank_optional_strings_fall_back_to_defaults() -> None:
    env = dict(BASE_ENV, SCHOOL_STUDENT_ID="  ", SCHOOL_HOST="")
    config = config_from_env(env)
    assert config.student_id == "sam"
    assert config.host == "127.0.0.1"


@pytest.mark.parametrize(
    "name",
    [
        "SCHOOL_DB_PATH",
        "SCHOOL_HAC_CACHE_PATH",
        "SCHOOL_MCP_TOKEN",
        "SCHOOL_DECK_REGISTRY",
    ],
)
def test_missing_required_variable_names_it(name: str) -> None:
    env = {key: value for key, value in BASE_ENV.items() if key != name}
    with pytest.raises(ConfigError, match=name):
        config_from_env(env)


@pytest.mark.parametrize(
    "name",
    [
        "SCHOOL_DB_PATH",
        "SCHOOL_HAC_CACHE_PATH",
        "SCHOOL_MCP_TOKEN",
        "SCHOOL_DECK_REGISTRY",
    ],
)
def test_empty_required_variable_names_it(name: str) -> None:
    env = dict(BASE_ENV, **{name: "  "})
    with pytest.raises(ConfigError, match=name):
        config_from_env(env)


@pytest.mark.parametrize("raw", ["abc", "87.5", "0", "-1", "65536"])
def test_invalid_port_names_school_port(raw: str) -> None:
    env = dict(BASE_ENV, SCHOOL_PORT=raw)
    with pytest.raises(ConfigError, match="SCHOOL_PORT"):
        config_from_env(env)


@pytest.mark.parametrize("raw", ["soon", "-5", "0"])
@pytest.mark.parametrize(
    "name", ["SCHOOL_STALE_HAC_HOURS", "SCHOOL_STALE_WEB_HOURS"]
)
def test_invalid_staleness_hours_name_the_variable(
    name: str, raw: str
) -> None:
    env = dict(BASE_ENV, **{name: raw})
    with pytest.raises(ConfigError, match=name):
        config_from_env(env)


def test_stale_thresholds_map_web_kinds_to_the_web_budget() -> None:
    env = dict(
        BASE_ENV, SCHOOL_STALE_HAC_HOURS="10", SCHOOL_STALE_WEB_HOURS="99"
    )
    thresholds = config_from_env(env).stale_thresholds
    assert thresholds == {
        SourceKind.HAC: 10.0,
        SourceKind.TEAM_SITE: 99.0,
        SourceKind.AGENDA: 99.0,
    }
