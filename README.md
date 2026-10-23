# School Information Adapter

Extends/wraps the HAC MCP into a general school-information adapter on
hal9k: pluggable **source drivers** feed two normalized concepts —

- **Drivers** (due items): anything with a date — what it is, which class, when it's due.
- **Resources**: notes, tutorial sessions, answer keys, links — the material that helps get the drivers done.

Records live in a normalized SQLite store and are exposed through an MCP
(`/mcp/school/` on the gateway): `get_drivers`, `get_resources`,
`get_source_health`. Every record carries `scraped_at` and every scrape
writes a run-log row, so consumers read staleness instead of guessing it.

Written for Sam first; the pattern generalizes to Aiden/Owen later.
Full product plan: [`docs/PLAN.md`](docs/PLAN.md). Engineering plan
(conventions C1–C10, module map, milestones): [`docs/DESIGN.md`](docs/DESIGN.md).

## Module map

One package per module under `src/school/`:

| Package | Responsibility | Patterns |
|---|---|---|
| `domain` | `DriverRecord`, `ResourceRecord`, `SourceRun`, `CourseRef`, value types | Frozen dataclasses, `Lookup[T]` union (`Found`/`Missing`) |
| `merge` | Dedup keys, cross-source record merger, precedence policy | Strategy (per-source-kind precedence), pure functions |
| `spi` | `SourceDriver` interface, `DriverRegistry`, `ScrapePipeline`, `RawPayload` | Strategy + Registry (C2), Pipeline (C3) |
| `store` | SQLite schema/migrations, repositories | Repository, composable query helpers |
| `driver_hac` | Wraps the existing grades cache → normalized records | Adapter |
| `driver_web` | Team-site hub crawler; agenda deck alt-text extractor; weekly-grid and tutorial-schedule parsers | Adapter, Strategy (per-deck-template parsers), pipeline stages as lambdas |
| `driver_classroom_web` | Playwright persistent profile; To-do page and Classwork scrapers | Adapter, Strategy (page extractors), session health probe |
| `vault` | Encrypted credential store, profile lifecycle | Encrypted-at-rest adapter, frozen config dataclass |
| `mcp_server` | Streamable-HTTP MCP endpoint; bearer auth matching the gateway | Thin adapter over query services; no logic in handlers |
| `app` | Composition root (manual DI), scheduler, health alerting | Composition root, Registry assembly |

Dependency direction: `app` → everything; `mcp_server`/drivers → `spi` +
`domain` + `store`; `domain`/`merge`/`spi` depend on nothing external.

## Build & verify

Requires Python ≥ 3.11. No runtime dependencies yet (M0 scaffold).

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
./scripts/verify.sh
```

`scripts/verify.sh` runs every quality gate in order and fails fast:

1. Unit tests — `pytest tests/unit`
2. Coverage — coverage.py over the unit tests, **`fail_under = 90`**
3. Type check — **mypy strict** over `src/school`
4. LOC cap — no `.py` file under `src/` over **500 lines**

System tests live in `tests_system/` (philosophy: mock externals only —
see `tests_system/README.md`). They gate merges but do not count toward
the unit-coverage gate.

## Development

Work happens on stacked milestone branches (`stack/m0-scaffold`,
`stack/m1-domain`, …), one agent per milestone, discrete single-purpose
commits — see `docs/DESIGN.md` §5. No milestone branch merges until its
gates are green.
