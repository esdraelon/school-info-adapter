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

## Architecture

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

### Data flow

```
grades-MCP cache file ── HacDriver ──────────┐
team-site hub ────────── TeamSiteDriver ─────┤
agenda decks ─────────── AgendaDriver ───────┼─► ScrapeRunner ─► merge ─► SQLite store
Classroom (browser) ──── ClassroomWebDriver ─┘        │                        │
                                                      └─ one run-log row      ▼
                                                         per kind per pass   QueryServices
                                                                                  │
                                              health ◄── get_source_health ◄──────┘
                                              (app/health verdicts over run facts)
```

One scrape pass runs every registered driver for the student, merges
sightings by precedence (CLASSROOM_WEB > HAC > AGENDA > TEAM_SITE,
then richer status, then freshest sighting), upserts the merged
records, and appends one run-log row per source
kind. A driver that fails produces a FAILURE/PARTIAL run row and the
pass continues — one dead source never sinks the others, and never
fails silently (the M7 drills in `tests_system/test_failure_drills.py`
pin this behavior).

## Build & verify

Requires Python ≥ 3.11.

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
5. System tests — `pytest tests_system` (mock externals only, C7;
   they gate merges but do not count toward unit coverage)

## Running the system

All entry points go through the composition root
(`python -m school.app`), configured from the environment:

```sh
python -m school.app serve    # the MCP over HTTP (uvicorn, SCHOOL_HOST:SCHOOL_PORT)
python -m school.app scrape   # one scrape pass; one line per source kind
python -m school.app health   # source health + verdicts as JSON
```

Exit codes: `scrape` exits 1 only when **every** kind failed;
`health` exits 0 when the health report is ok (warnings allowed), 1
otherwise; configuration errors exit 2 with the offending variable
named. Deployment artifacts (Dockerfile, compose file, scrape timer,
nginx snippet, runbook) live in [`deploy/`](deploy/README.md).

### Configuration

Required keys have no default — a missing one fails startup loudly.
Everything else defaults to the production value.

| Key | Required | Default | Meaning |
|---|---|---|---|
| `SCHOOL_DB_PATH` | ✓ | — | SQLite store path |
| `SCHOOL_HAC_CACHE_PATH` | ✓ | — | The grades-MCP cache file to read (read-only) |
| `SCHOOL_MCP_TOKEN` | ✓ | — | Bearer token the MCP requires (the gateway token) |
| `SCHOOL_DECK_REGISTRY` | ✓ | — | Deck-registry JSON path (see operational notes) |
| `SCHOOL_STUDENT_ID` | | `sam` | Student the drivers serve |
| `SCHOOL_STUDENT_NAME` | | `Sam` | Display name for that student |
| `SCHOOL_TIMEZONE` | | `America/Chicago` | School-local zone; HAC due-relative statuses derive against its calendar date |
| `SCHOOL_TEAM_SITE_URL` | | the Leander ISD 8th-grade hub | Team-site hub URL |
| `SCHOOL_HOST` / `SCHOOL_PORT` | | `127.0.0.1` / `8765` | MCP bind address |
| `SCHOOL_STALE_HAC_HOURS` | | `30` | HAC staleness budget for health verdicts |
| `SCHOOL_STALE_WEB_HOURS` | | `192` | Team-site/agenda staleness budget |
| `SCHOOL_STALE_CLASSROOM_HOURS` | | `30` | Classroom staleness budget (when configured) |
| `SCHOOL_CLASSROOM_PROFILE_DIR` | | — | Playwright profile dir; with `SCHOOL_CLASSROOM_ACCOUNT`, enables the Classroom driver |
| `SCHOOL_CLASSROOM_ACCOUNT` | | — | The Classroom account (vault entry key + expected login) |
| `SCHOOL_VAULT_DIR` | | — | Vault dir (`vault.json` + `vault.key`); enables vault-backed re-auth |

## Operational notes

- **The MCP SDK is pinned `mcp>=1.16,<2` on purpose.** The 2.x line
  renames/restructures FastMCP; bumping the pin is a conscious
  decision with its own verification pass, not a drive-by upgrade.
- **`school.mcp_server.main()` is a legacy standalone entry** kept
  from M4: it wires `QueryServices` directly from its own
  `SCHOOL_MCP_PORT` / `SCHOOL_MCP_TOKEN` / `SCHOOL_MCP_DB_PATH` env
  names. Those names are **superseded** by the app CLI's `SCHOOL_*`
  names above (only `SCHOOL_MCP_TOKEN` coincides — it is the token
  in both schemes). Production runs `school.app serve`; removing the
  legacy `main()` is a cleanup candidate once nothing references it.
- **Production deck-registry rule: EXCLUDE the tutorial deck.** The
  M3 discovery registry
  (`tests/unit/driver_web/fixtures/deck_registry.json`) lists a
  fourth deck, *Tutorial Schedule* (`tutorial-table-v1`). Tutorials
  are **not** an Agenda source — they flow through the TeamSiteDriver
  (from the hub's embeds) as TUTORIAL resources. No
  `tutorial-table-v1` parser is registered with the AgendaDriver, so
  a production registry copied verbatim from discovery makes AGENDA
  sit at PARTIAL forever ("unknown template"). The production
  registry contains exactly Math 8, Algebra I, and Team Agenda.
- **Real-Chromium validation debt.** The Classroom driver's
  Playwright adapter layer (`driver_classroom_web/session.py`) is
  fully unit-covered against protocol fakes, and the real-Chromium
  system suite exists (`tests_system/test_classroom_web.py`) — but in
  this dev environment it cannot run (Playwright's Chromium download
  fails; the system Chromium refuses local navigations), so those
  tests skip here. The adapter's first true execution happens at
  profile birth on hal9k: run the suite there and recapture the
  Classroom fixtures from the real pages per `docs/FIXTURES.md`
  before trusting Classroom data.
- **The vault is verified at boot.** When `SCHOOL_VAULT_DIR` is set,
  the composition root unlocks the vault **and decrypts every entry**
  (`Vault.verify()`): a swapped or wrong key file fails the build
  loudly instead of surfacing mid-scrape (drill D4).
- **HAC statuses are school-local.** The HAC cache carries no
  turned-in/missing flags; the driver derives MISSING from unscored +
  past-due against the `SCHOOL_TIMEZONE` calendar date, so an item
  due today stays ASSIGNED through the school evening (it previously
  flipped at 19:00 local under a UTC reference date).

## Post-deploy

- Soak plan (what to watch for 1–2 weeks, digest v1/v2 parallel run,
  exit criteria): [`docs/SOAK-PLAN.md`](docs/SOAK-PLAN.md)
- Retiring the legacy grades-only path after soak:
  [`docs/LEGACY-DECOMMISSION.md`](docs/LEGACY-DECOMMISSION.md)
- Digest v2 cutover spec: [`deploy/DIGEST_V2.md`](deploy/DIGEST_V2.md)

## Development

Work happens on stacked milestone branches (`stack/m0-scaffold`,
`stack/m1-domain`, …), one agent per milestone, discrete single-purpose
commits — see `docs/DESIGN.md` §5. No milestone branch merges until its
gates are green.
