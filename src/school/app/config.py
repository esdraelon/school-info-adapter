"""Application configuration from the environment.

Pattern: pure config builder (C2/C3) — ``config_from_env`` maps an
explicit environment mapping to a frozen ``AppConfig``; nothing here
reads ``os.environ`` (the CLI shim owns that), so every branch is
unit-testable without monkeypatching the process environment.

Required values that are missing, empty, or unparseable raise
``ConfigError`` with a message naming the offending variable — a
misconfigured deployment must fail loudly at startup, never fall back
to a plausible-looking default.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from school.domain import SourceKind

#: The real team-site hub (M3 discovery fixture): the production default.
DEFAULT_TEAM_SITE_URL = (
    "https://sites.google.com/leanderisd.org/8th-grade-team-site/home"
)
DEFAULT_STUDENT_ID = "sam"
DEFAULT_STUDENT_NAME = "Sam"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_STALE_HAC_HOURS = 30.0
DEFAULT_STALE_WEB_HOURS = 192.0
DEFAULT_STALE_CLASSROOM_HOURS = 30.0
#: The school's timezone: HAC due dates are school-local, and the HAC
#: driver's status derivation reads "today" in this zone (M7).
DEFAULT_TIMEZONE = "America/Chicago"


class ConfigError(Exception):
    """The environment cannot produce a valid AppConfig."""


@dataclass(frozen=True, kw_only=True)
class AppConfig:
    """Everything the composition root needs, in one frozen value."""

    db_path: str
    hac_cache_path: str
    token: str
    deck_registry_path: str
    student_id: str = DEFAULT_STUDENT_ID
    student_name: str = DEFAULT_STUDENT_NAME
    timezone_name: str = DEFAULT_TIMEZONE
    team_site_url: str = DEFAULT_TEAM_SITE_URL
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    stale_hac_hours: float = DEFAULT_STALE_HAC_HOURS
    stale_web_hours: float = DEFAULT_STALE_WEB_HOURS
    classroom_profile_dir: str | None = None
    classroom_account: str | None = None
    vault_dir: str | None = None
    stale_classroom_hours: float = DEFAULT_STALE_CLASSROOM_HOURS

    @property
    def classroom_configured(self) -> bool:
        """The Classroom driver is wired only when BOTH the profile
        dir and the account are set; either alone leaves it off."""
        return bool(self.classroom_profile_dir and self.classroom_account)

    @property
    def stale_thresholds(self) -> dict[SourceKind, float]:
        """Staleness budget per source kind, in hours.

        HAC gets the tight budget (its cache is refreshed by the
        grades scraper on school-day cadence); the web sources publish
        weekly, so their budget is a week plus a day of slack.
        CLASSROOM_WEB joins with the tight budget only when the
        classroom driver is configured — unconfigured kinds have no
        staleness verdict.
        """
        thresholds = {
            SourceKind.HAC: self.stale_hac_hours,
            SourceKind.TEAM_SITE: self.stale_web_hours,
            SourceKind.AGENDA: self.stale_web_hours,
        }
        if self.classroom_configured:
            thresholds[SourceKind.CLASSROOM_WEB] = (
                self.stale_classroom_hours
            )
        return thresholds


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value.strip():
        raise ConfigError(f"{name} is required and must not be empty")
    return value


def _optional_string(env: Mapping[str, str], name: str, default: str) -> str:
    value = env.get(name, "")
    return value if value.strip() else default


def _optional_value(env: Mapping[str, str], name: str) -> str | None:
    """An optional setting with no default: None when unset/empty."""
    value = env.get(name, "")
    return value if value.strip() else None


def _timezone_name(env: Mapping[str, str]) -> str:
    name = _optional_string(env, "SCHOOL_TIMEZONE", DEFAULT_TIMEZONE)
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(
            f"SCHOOL_TIMEZONE must be an IANA timezone name, got {name!r}"
        ) from None
    return name


def _port(env: Mapping[str, str]) -> int:
    raw = env.get("SCHOOL_PORT", "")
    if not raw.strip():
        return DEFAULT_PORT
    try:
        port = int(raw)
    except ValueError:
        raise ConfigError(
            f"SCHOOL_PORT must be an integer, got {raw!r}"
        ) from None
    if not 1 <= port <= 65535:
        raise ConfigError(f"SCHOOL_PORT must be in 1..65535, got {port}")
    return port


def _hours(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "")
    if not raw.strip():
        return default
    try:
        hours = float(raw)
    except ValueError:
        raise ConfigError(
            f"{name} must be a number of hours, got {raw!r}"
        ) from None
    if hours <= 0:
        raise ConfigError(f"{name} must be positive, got {hours}")
    return hours


def config_from_env(env: Mapping[str, str]) -> AppConfig:
    """Build an AppConfig from environment variables (see module doc).

    Required: SCHOOL_DB_PATH, SCHOOL_HAC_CACHE_PATH, SCHOOL_MCP_TOKEN,
    SCHOOL_DECK_REGISTRY. Everything else has a production default —
    including SCHOOL_TIMEZONE (America/Chicago), the school-local zone
    the HAC driver derives due-relative statuses against.
    The Classroom keys are optional as a set: SCHOOL_CLASSROOM_PROFILE_DIR
    + SCHOOL_CLASSROOM_ACCOUNT enable the driver (SCHOOL_VAULT_DIR adds
    vault-backed re-auth); absent keys leave it unconfigured.
    """
    return AppConfig(
        db_path=_required(env, "SCHOOL_DB_PATH"),
        hac_cache_path=_required(env, "SCHOOL_HAC_CACHE_PATH"),
        token=_required(env, "SCHOOL_MCP_TOKEN"),
        deck_registry_path=_required(env, "SCHOOL_DECK_REGISTRY"),
        student_id=_optional_string(
            env, "SCHOOL_STUDENT_ID", DEFAULT_STUDENT_ID
        ),
        student_name=_optional_string(
            env, "SCHOOL_STUDENT_NAME", DEFAULT_STUDENT_NAME
        ),
        timezone_name=_timezone_name(env),
        team_site_url=_optional_string(
            env, "SCHOOL_TEAM_SITE_URL", DEFAULT_TEAM_SITE_URL
        ),
        host=_optional_string(env, "SCHOOL_HOST", DEFAULT_HOST),
        port=_port(env),
        stale_hac_hours=_hours(
            env, "SCHOOL_STALE_HAC_HOURS", DEFAULT_STALE_HAC_HOURS
        ),
        stale_web_hours=_hours(
            env, "SCHOOL_STALE_WEB_HOURS", DEFAULT_STALE_WEB_HOURS
        ),
        classroom_profile_dir=_optional_value(
            env, "SCHOOL_CLASSROOM_PROFILE_DIR"
        ),
        classroom_account=_optional_value(env, "SCHOOL_CLASSROOM_ACCOUNT"),
        vault_dir=_optional_value(env, "SCHOOL_VAULT_DIR"),
        stale_classroom_hours=_hours(
            env,
            "SCHOOL_STALE_CLASSROOM_HOURS",
            DEFAULT_STALE_CLASSROOM_HOURS,
        ),
    )
