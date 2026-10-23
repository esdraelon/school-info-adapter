#!/usr/bin/env bash
# Quality gates for the School Information Adapter (design plan C5/C6).
# Runs every gate in order and fails fast on the first failure.
#
# Assumes the dev dependencies are installed in the active environment:
#   python3 -m venv .venv && source .venv/bin/activate
#   pip install -e '.[dev]'
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> [1/5] Unit tests"
python -m pytest tests/unit

echo "==> [2/5] Coverage gate (fail_under = 90, see pyproject.toml)"
python -m coverage run -m pytest tests/unit
python -m coverage report

echo "==> [3/5] Type check (mypy strict)"
python -m mypy src/school

echo "==> [4/5] LOC cap (500 lines per file under src/)"
python scripts/check_loc.py src

echo "==> [5/5] System tests (end-to-end; gate merges but do not count toward unit coverage, C6/C7)"
python -m pytest tests_system

echo "All gates passed."
