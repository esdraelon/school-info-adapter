"""Application assembly: composition root (manual DI), scrape orchestration, health alerting.

Patterns: Composition root; Registry assembly; thin CLI shim over the
wired graph (school.app.cli).
"""

from school.app.composition import App, build_app, load_deck_configs
from school.app.config import AppConfig, ConfigError, config_from_env
from school.app.health import (
    HealthEntry,
    HealthReport,
    HealthStatus,
    evaluate_health,
)
from school.app.scrape_runner import ScrapeRunner

__all__ = [
    "App",
    "AppConfig",
    "ConfigError",
    "HealthEntry",
    "HealthReport",
    "HealthStatus",
    "ScrapeRunner",
    "build_app",
    "config_from_env",
    "evaluate_health",
    "load_deck_configs",
]
