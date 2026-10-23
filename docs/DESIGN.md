# School Information Adapter — Design & Build Plan

**Date:** 2026-10-22 · **Companion:** `PLAN.md` (product plan; Stage 0 findings are settled there)
**Status:** For Noah's approval; Milestone 0 dispatched on this plan.

---

## 1. Language decision

**Python 3.** *(Final, 2026-10-22 evening. Superseded along the way: Java 21 — resident JVM footprint for an intermittent service; PHP 8.5 + `amtgard/builder-traits` — solid, but its ecosystem gravity is the Amtgard estate on a different host, and its browser client is the weakest of the three. Noah's deciding argument: the hal9k estate this system joins — grades MCP, todo MCP, the HAC code being wrapped — is already Python, and he explicitly dropped the Optional and Builder conventions, which were the two rules fighting Python's grain.)*

| Requirement | Python fit |
|---|---|
| DI + strategy, no if/else config selection | Dict-of-callables registries + constructor injection — arguably the most natural fit of any candidate |
| Composition, higher-order lambdas | Native first-class functions everywhere |
| ≥90% unit coverage, enforced | pytest + coverage.py `fail_under = 90` in the verify gate |
| Full-system tests, mock only externals | pytest system suite booting the real stack (real SQLite, real MCP HTTP); externals served as fixtures by a local stub server |
| Static rigor | **mypy strict** in the gate (the PHPStan-discipline role) |
| MCP server | Official Python MCP SDK (FastMCP) — the same stack the existing gateway MCPs already run |
| Store | SQLite via stdlib `sqlite3` — zero new dependencies |
| Browser driver (M6) | **Playwright for Python is first-class** (reference implementation) — the M6 risk PHP carried simply evaporates |
| Runs on hal9k | Same deployment shape as the existing MCP containers; wake-scrape-sleep, ~zero idle footprint |

**Conventions dropped at Noah's direction (2026-10-22), with their intent preserved by Python idiom:**
- **Optional (was C1)** → absence is `X | None` with explicit narrowing, kept honest by mypy strict. No monad wrapper.
- **Builder (was C4)** → construction is frozen dataclasses + keyword arguments; a fluent builder is permitted only where construction is genuinely staged.

Build: **pyproject.toml**, one package tree with a package per module (§3). Manual constructor DI from a single composition root.

## 2. Codified conventions (acceptance criteria, not vibes)

- **C1 — Absence discipline** *(revised 2026-10-22: the Optional-type convention was dropped with the Python decision).* Absence is expressed as `X | None` in type hints with explicit narrowing at the point of use — no sentinel objects, no `Optional` wrapper type. Single-item lookups that can miss return a `Lookup[T]` union (`Found(value)` / `Missing(reason)`, two frozen dataclasses matched exhaustively); collection queries return empty collections. mypy strict enforces that every `None` case is handled. *(Lookup flagged as Decision D-1 — veto if you want plain `T | None` everywhere instead.)*
- **C2 — No if/else for config-driven selection.** Driver selection, parser selection, and merger-policy selection resolve through injected registries (`Map<SourceKind, SourceDriver>` etc.) populated by the composition root from config. Conditionals remain legal for business logic and validation — just never "which implementation do I use."
- **C3 — Composition over inheritance.** The scrape lifecycle is a composed function pipeline (fetch → extract → parse → normalize → persist → log), assembled from injected lambdas/components. No abstract base classes with template methods.
- **C4 — Construction** *(revised 2026-10-22: the blanket Builder convention was dropped with the Python decision).* Domain objects are frozen dataclasses constructed with keyword arguments. A fluent builder class is permitted only where construction is genuinely staged (multi-step assembly with intermediate validation), and must be justified in its module doc.
- **C5 — 500 LOC hard cap per file**, enforced by a check script in the verify script (`scripts/verify.sh`). A file approaching the cap gets split by responsibility, not compressed.
- **C6 — Coverage gate.** pytest + coverage.py: ≥90% line coverage per module on unit tests (`fail_under = 90`), enforced by the verify script; **mypy strict** runs in the same gate. System tests live in a separate `tests_system/` suite — they gate merges but don't inflate unit coverage.
- **C7 — System tests mock externals only.** Real: store (SQLite), MCP transport (real HTTP), parsers, merge logic, Playwright + real Chromium. Mocked at the boundary with a local stub HTTP server: HAC, Google Sites/Slides endpoints, and classroom.google.com (served as captured fixture HTML through the real browser). No mocking of internal collaborators in system tests; unit tests may mock immediate collaborators only.
- **C8 — Discrete commits on stacked branches.** One branch per milestone, stacked (see §5). Commits are single-purpose with imperative subject lines. No milestone branch merges until its checklist section is green.
- **C9 — Every class/module names its pattern** in its docstring header (Strategy, Registry, Repository, Adapter, Pipeline stage, Composition root…). If a class has no pattern, that's a smell to resolve in review.
- **C10 — Freshness is data.** Every record carries `scrapedAt`; every scrape writes a run-log row (success or failure). Consumers read staleness, never guess it. (Lesson of the HAC cache.)

## 3. Module map (each module = one responsibility; patterns named)

| Module | Contents | Patterns |
|---|---|---|
| `domain` | `DriverRecord`, `ResourceRecord`, `SourceRun`, `CourseRef`, value types | Frozen dataclasses, `Lookup[T]` union (`Found`/`Missing`) |
| `merge` | Dedup keys, cross-source record merger, precedence policy | Strategy (per-source-kind precedence), pure functions |
| `spi` | `SourceDriver` interface, `DriverRegistry`, `ScrapePipeline`, `RawPayload` | Strategy + Registry (C2), Pipeline (C3) |
| `store` | SQLite schema/migrations, `DriverRepository`, `ResourceRepository`, `RunLogRepository` | Repository, composable query helpers |
| `driver-hac` | Wraps the existing grades cache → normalized records | Adapter |
| `driver-web` | Team-site hub crawler; agenda deck alt-text extractor; weekly-grid parser; tutorial-schedule parser | Adapter, Strategy (per-deck-template parsers, registry-selected), Pipeline stages as lambdas |
| `driver-classroom-web` | Playwright persistent profile; To-do page scraper; Classwork scraper | Adapter, Strategy (page extractors), session health probe |
| `vault` | Encrypted credential store (age-encrypted file, service-user-only perms), profile lifecycle | Encrypted-at-rest adapter, frozen config dataclass |
| `mcp-server` | Streamable-HTTP MCP endpoint; tools `get_drivers`, `get_resources`, `get_source_health`; bearer auth matching the gateway | Thin adapter over query services; no logic in handlers |
| `app` | Composition root (manual DI), scheduler (scrape cadence), health alerting | Composition root, Registry assembly |

Dependency direction: `app` → everything; `mcp-server`/`drivers` → `spi` + `domain` + `store`; `domain`/`merge`/`spi` depend on nothing external. Drivers never talk to each other; merging happens only in `merge`.

## 4. Testing strategy

- **Unit tests** (pytest; mocks only for immediate collaborators): every parser fixture-driven; merge/precedence logic table-tested; registry selection tested to prove C2 (swap config → swap behavior, no code change).
- **System tests** (`tests_system/`): boot the real composition root with fixture-served externals (local stub HTTP server) + temp SQLite + real MCP HTTP calls end-to-end (scrape → store → query via MCP tool call → assert records). Classroom system test drives **real Playwright/Chromium** against stub-served captured Classroom HTML.
- **Fixtures are captured reality, not hand-written HTML**: agenda deck pub-HTML, team-site HTML, and the Classroom To-do accessibility-tree spec (captured 2026-10-22 → `fixtures/classroom-todo-a11y-map.md`; raw HTML fixtures get captured by the real driver at M6).
- Coverage: coverage.py per-module gates wired into the verify script from M1 onward (M0 installs the gate; it binds as soon as code exists).

## 5. Milestones — stacked branches, one agent each

Branch stack (each branches off the previous; discrete commits within):

1. **`stack/m0-scaffold`** — git repo, pyproject-based Python package skeleton (one package per §3 module), pytest + coverage.py 90% gate + mypy-strict + LOC-cap wiring in a verify script, one smoke module+test proving the toolchain, `docs/` with this plan. *No business logic.* (Two earlier M0 dispatches — Java/Gradle, then a PHP plan — were superseded the same evening by the language decisions; total cost was one partial scaffold, discarded.)
2. **`stack/m1-domain`** — `domain`, `merge`, `spi`, `store`: frozen-dataclass models, `Lookup[T]`, dedup/merge with precedence strategies, driver registry (DI proof), SQLite repositories + migrations. ≥90% unit coverage.
3. **`stack/m2-hac-driver`** — `driver-hac` adapter over the existing grades cache; run-log writes; staleness surfacing.
4. **`stack/m3-web-drivers`** — `driver-web`: team-site crawler, agenda alt-text parser (math deck fixture first), tutorial schedule → resources; per-template parser registry.
5. **`stack/m4-mcp-server`** — `mcp-server` + query services; first full system test (stub-server externals → scrape → store → MCP query).
6. **`stack/m5-scheduling`** — `app` composition root completed; scrape scheduler; health alerts; digest v2 cutover behind the existing gateway route plan (`/mcp/school/`).
7. **`stack/m6-classroom-web`** — `vault` + `driver-classroom-web`: encrypted credential vault on hal9k, Playwright persistent profile (born on hal9k under Xvfb, `--password-store=basic`), To-do + Classwork extractors, session health probe + re-auth escalation. System test on captured fixtures with real Chromium.
8. **`stack/m7-hardening`** — failure drills (session death mid-run, deck format drift, HAC staleness), soak, docs, decommission notes for the legacy grades-only path.

Each milestone agent gets: this plan, the product plan, its milestone spec, and the prior milestone's summary — nothing else. It reports: commits, coverage numbers, LOC audit, open issues. The main agent reviews before the next milestone dispatches.

## 6. Open decisions

- **D-1** — C1's `Lookup[T]` boundary union (vs. plain `T | None` returns everywhere). Default: proceed as written.
- **D-2** — Repo hosting: local git on Haliffer's workspace, mirrored to hal9k at deploy time. (No GitHub remote exists for this; say if you want one.)
- **D-3** — Language: **RESOLVED 2026-10-22 — Python 3** (Noah's call: hal9k estate fit + Playwright; Optional and Builder conventions dropped — see §1). `Lookup[T]` in Python is a union of two frozen dataclasses, matched exhaustively under mypy strict.
