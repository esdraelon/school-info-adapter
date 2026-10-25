# Split design — school-info-adapter: ingestion pipeline × serving MCP

**Status:** design only, decisive. No code, no production
change, nothing merged. Branch: `stack/split-design`.
Sequencing decisions in §9 are DECISIVE — they govern
orchestration once the dispatcher reviews this design + plan,
and are overturned only if the dispatcher finds a flaw (in
which case the design is amended first, then work proceeds).
**Companion:** `docs/SPLIT-PLAN.md` (sequenced milestones).

## 0. Two premise corrections (evidence first)

The split brief described two things that the code does not do.
The design below is built on what the code actually does:

1. **Merge does NOT happen at query time.** `ScrapeRunner.run`
   (`src/school/app/scrape_runner.py`) calls
   `upsert_drivers(merge_driver_records(sightings))` — the merge
   (precedence + tiebreaks, `merge/engine.py`) runs in the scrape
   flow, before persistence. `QueryServices`
   (`mcp_server/services.py`) states it explicitly: "The store
   already holds the *merged* view (the scrape flow merges before
   upserting), so nothing here re-merges." Serving is ALREADY a
   pure reader over a merged store. The split therefore does not
   move merge logic — it separates the processes, images, and
   composition roots around a store that is already materialized.
2. **`get_source_health` does NOT live-probe sources.**
   `QueryServices.source_health` reads `RunLogRepository.latest_run`
   per kind and derives age by clock arithmetic (C10). Health is
   already read-data from ingestion run records. The split
   preserves this and makes it structural: serving never imports
   a driver, a fetcher, or the vault, so it *cannot* probe.

What is actually bundled today is narrower — and therefore more
fixable — than "ingestion + serving logic": one composition root
(`app/composition.py`) builds both the scrape runner and the MCP
server; one container runs the server always-on AND takes scrape
passes via `docker exec` from the timer; one image, one CLI
(`school.app serve|scrape|health`), one verify suite. An agent
working on a driver shares a repo, a root, and a deployable with
an agent working on serializers. That shared context is the muddiness
this split removes.

## 1. Target shape (recommendation)

**One repo, two deployables, one shared kernel.** This mirrors
the school-index estate pattern (one repo; timer-driven pipeline
and serving MCP as separate processes sharing only the corpus DB
plus a WAL contract) and the settled `school-assistant` decision
that `school-core` extraction waits for a second consumer
(`docs/DECISIONS.md`): there is still exactly one consumer of
these domain types, so splitting the repo would create version
skew over `DriverRecord` with no second consumer to justify it.

- **Ingestion side** — package area: `driver_hac/`, `driver_web/`,
  `driver_classroom_web/`, `vault/`, `merge/`, `spi/` (driver
  contract), `app/scrape_runner.py`, plus a NEW pipeline-only
  composition root and CLI entry. Image: Chromium-baked
  (Playwright + browsers). Runs: oneshot, on the timer.
- **Serving side** — package area: `mcp_server/` (server,
  services, serializers, auth) plus a health-evaluation helper.
  Image: slim Python, no browsers, no vault, no drivers.
  Runs: always-on container on 127.0.0.1:8765.
- **Shared kernel** — `domain/`, `store/` (schema + repositories),
  and the interface documents (this design + the MCP contract in
  `deploy/README.md`). Neither side owns the kernel alone:
  kernel changes are joint milestones with both sides' contract
  tests as gates (see §7, change protocol).

**Not recommended now: two repos.** It would force a third
`school-core` package for the domain/store types, a versioning
story for schema changes, and duplicate fixture/tooling — all to
solve a context problem that directory + interface discipline
already solves. Revisit if a genuine second consumer of the
stores appears, or if kernel changes start being driven by
serving-only needs. That trigger, not a date, is the revisit
condition.

## 2. Ingestion side (pipeline)

**Owns:** every driver; the vault; the merge policy invocation;
the scrape runner; the run log; all upstream mounts and configs.

- **Entry:** new `school.pipeline` composition root + CLI
  (`pipeline scrape` as the oneshot). It builds the driver
  registry + `ScrapeRunner` only — no MCP, no token, no uvicorn,
  no serving config. The old `school.app scrape` path is retired
  at cutover (see plan M4); until then the pipeline entry must
  prove row-identical output to the old path (plan M1 gate).
- **Image:** `haliffer-school-pipeline`, built from the
  birth-staging Dockerfile fix (Playwright + Chromium baked in;
  `PLAYWRIGHT_BROWSERS_PATH` + `playwright install --with-deps
  chromium`). The `:birth` and `:birth-ui` images become
  pipeline-image variants (birth-ui = pipeline image + Xvfb +
  x11vnc, unchanged in purpose).
- **Runs:** systemd timer, same schedule (every 6h at :15),
  repointed from `docker exec haliffer-school-mcp … scrape` to
  `docker run --rm haliffer-school-pipeline` (oneshot; the
  container exits when the pass ends — a stuck scrape can no
  longer wedge the serving container, and a serving restart
  can no longer kill a scrape).
- **Mounts (moves here, off the serving container):**
  grades-cache read-only (`/var/www/haliffer/grades-cache`),
  deck registry read-only, Classroom profile dir, vault dir.
- **Config (pipeline only):** `SCHOOL_DB_PATH`,
  `SCHOOL_HAC_CACHE_PATH`, `SCHOOL_DECK_REGISTRY`,
  `SCHOOL_STUDENT_ID/NAME`, `SCHOOL_TIMEZONE`,
  `SCHOOL_TEAM_SITE_URL`, stale-hour budgets for run
  evaluation, `SCHOOL_CLASSROOM_PROFILE_DIR/ACCOUNT`,
  `SCHOOL_VAULT_DIR`. No token, no host/port.
- **Security improvement the split buys:** today the shared
  composition root unlocks + verifies the vault at build time
  even for `serve` — the serving process touches credentials it
  never uses. After the split, serving never sees the vault
  dir, the profile dir, or any credential material. The vault
  unlock moves to pipeline build only.
- **Failure containment (unchanged):** one dead source yields
  one FAILURE run row and never sinks the others
  (`scrape_runner.py` rules); exit 1 only when every kind
  failed or none ran.

## 3. Serving side (MCP)

**Owns:** the MCP tools, serializers, auth, health evaluation.
It owns no driver, no fetcher, no browser, no credential.

- **Entry:** `mcp_server/server.py:main` already exists and is
  already standalone — it builds `QueryServices` from a DB path
  + token + port, with no composition-root, driver, or vault
  involvement. The split promotes this to THE serving entry;
  `school.app serve` is retired at cutover. No new serving
  code is needed for the entry itself — only the slim image
  and compose file around it.
- **Image:** `haliffer-school-mcp` rebuilt slim (current
  production Dockerfile minus nothing else needed — it already
  has no Chromium on main; the split simply guarantees that
  stays true by construction: the serving Dockerfile must not
  install Playwright, and a build-time check fails the image
  if a browser binary is present).
- **Mounts (only this):** the store volume (see §4 for the WAL
  sidecar rule). No grades-cache, no deck registry, no
  profile, no vault. The serving container cannot see a
  credential even if misconfigured — there is nothing to see.
- **Config (serving only):** `SCHOOL_MCP_PORT`,
  `SCHOOL_MCP_TOKEN`, `SCHOOL_MCP_DB_PATH`, stale-hour
  thresholds for health evaluation. No student, no timezone,
  no driver config: serving is student-agnostic (the student
  arrives as a tool parameter, as today).
- **Read-only by construction:** the server issues SELECTs
  only — no upsert, no migrate-on-serve path, no schema
  creation. A system test proves serving moves no rows
  (mirroring the school-index serving test); that test is a
  merge gate for the serving side forever, not a one-off.
- **Health CLI:** `school.app health` is retired at cutover.
  The soak watch and deploy smokes use gateway
  `get_source_health` instead (they already may — SOAK-PLAN
  allows either). Threshold evaluation stays as a serving-side
  helper (same rules the digest duplicates in DIGEST_V2.md);
  the digest's duplicate is unchanged by the split.

## 4. The interface (the contract both sides develop against)

This section is the whole split. If it holds, the sides never
need each other's context. It freezes the CURRENT store and
contract — the split introduces no behavior change, only
process separation.

### 4a. Drivers store — `drivers` table (frozen v1 schema)

Columns (see `store/schema.py`): `dedup_key` (PK),
`student_id`, `student_name`, `course_name`,
`course_period`, `course_teacher`, `title`, `kind`, `due_at`
(nullable), `status` (name), `score_text` (verbatim source
text, never normalized to a number), `source_kind`,
`source_ref`, `scraped_at` (ISO-8601 UTC text).

Semantics (binding):

- One row per dedup key = the MERGED view, not sightings.
  Sightings never reach this table; only merged records do.
- `source_kind` = the winning sighting's kind;
  `scraped_at` = the MAX `scraped_at` across the group (C10:
  the merged view is exactly as fresh as its freshest sighting).
- Winner selection (merge policy, ingestion-side, already
  implemented in `merge/engine.py`): precedence
  CLASSROOM_WEB (3) > HAC (2) > AGENDA (1) > TEAM_SITE (0),
  unlisted kinds rank below all; ties break by status
  richness (GRADED > DONE > MISSING > ASSIGNED, the enum's
  own ordering), then by most recent `scraped_at`.
- `due_at` NULL means posted-but-undated; it is data, not a
  gap to fill. Serving must not invent a due date.
- `score_text` stays verbatim; no side invents a numeric
  score model (a separate decision if ever wanted).

### 4b. Resources store — `resources` table (frozen v1 schema)

Columns: `natural_key` (PK), `student_id` (nullable = shared),
`student_name`, `course_name`, `course_period`,
`course_teacher`, `title`, `kind`, `url`, `source_kind`,
`scraped_at`. Identity = owner + normalized course + normalized
title + URL (verbatim) + source kind (JSON key, see
`repositories.py:_resource_key`). Dedupe rule: one row per
natural key, freshest sighting wins. Serving's visibility rule
(own + shared, courseless matches any course) is a read-side
rule over this table, unchanged.

### 4c. Source-health records — `source_runs` table

Columns: `id`, `source_kind`, `started_at`, `finished_at`,
`outcome` (SUCCESS / PARTIAL / FAILURE), `message`,
`records_found/new/updated`. One row per attempted kind per
pass, including failures (C10) — a kind with no registered
driver still leaves a FAILURE row naming the reason, and a
driver raising still leaves a FAILURE row naming the exception.

Serving derivation (binding, already implemented in
`QueryServices.source_health`): per kind, take the latest run
by (`finished_at`, `id`); report its outcome, finish stamp,
age in seconds vs the caller's clock, and message. A kind
with no run reports all-null (never-run is distinct from
failed). Serving NEVER probes a source, a cache file, or a
browser to answer health. The digest's staleness thresholds
(HAC 30h, web 192h, Classroom 30h when configured) stay
evaluation policy in the caller, not in this table.

### 4d. Ownership matrix

| Artifact | Ingestion writes | Serving reads | Serving writes |
|---|---|---|---|
| `drivers` rows | upsert merged | SELECT | never |
| `resources` rows | upsert deduped | SELECT | never |
| `source_runs` rows | append | SELECT latest | never |
| `schema_migrations` | migrate at pipeline build | none (serving must not migrate) | never |
| DB file + WAL sidecars | rw | rw on sidecars ONLY (WAL mechanics), SELECTs only | never |

### 4e. Coexistence: WAL + ACLs (a real change, not a freeze)

Today the store opens with SQLite's default journal mode —
fine for one process, wrong for two. The split adds what the
school-index already proved:

- **WAL mode, set by the pipeline** (`PRAGMA
  journal_mode=WAL`, persistent on the DB file; add as schema
  migration v2 so it is applied once and recorded, not
  re-applied per connection). Serving benefits without
  setting it.
- **Short-lived connections stay** (repositories already open
  per operation — no long-lived locks to fight over).
- **ACLs on the store volume** (mirror index deploy/README):
  data dir owned by the pipeline user; serving uid 10001 gets
  rw on the DB file AND the `-wal`/`-shm` sidecars via default
  ACLs (a WAL reader needs sidecar write access even though
  it never writes rows). If the pipeline ever reports
  "attempt to write a readonly database", check sidecar ACLs
  first — the DB file itself will look writable.
- **Read-only proof test** (serving side, merge gate): boot
  the server against a fixture DB, run every tool, assert
  row counts + checksums unchanged.

### 4f. MCP contract preservation (binding, no behavior change)

Tool surface, params, ordering, limits, and wire shapes stay
EXACTLY as documented in `deploy/README.md` ("MCP collection
contract — pagination & coverage"):

- Tools: `get_drivers(student_id, course_name?, status?,
  due_on_or_before?, due_on_or_after?, limit=200)`,
  `get_resources(student_id?, course_name?, kind?,
  limit=200)`, `get_source_health()`. No renames, no new
  required params, no new tools in the split.
- Ordering: drivers `due_at IS NULL, due_at, title` (dated
  oldest-first, undated last); resources by `title`.
- Limit applied AFTER filters, default 200, silent
  truncation (bare list, no total, no `has_more`). A page of
  exactly 200 is possibly-truncated, never complete.
- Window semantics: a due window EXCLUDES undated items;
  undated items need a separate unwindowed call. Course
  filters match courseless rows (shared material) — unions
  dedupe by student + URL before any total comparison.
- Unknown arguments are IGNORED (not rejected) — a wart,
  preserved deliberately. Fixing it is a separate contract
  decision with its own migration, NOT smuggled into the split.
- Wire enums by name: statuses ASSIGNED/MISSING/DONE/GRADED;
  kinds NOTES/TUTORIAL/LINK/DOCUMENT; sources HAC/TEAM_SITE/
  AGENDA/CLASSROOM_WEB. Serializers (`driver_to_dict`,
  `resource_to_dict`, `source_health_to_dict`) are unchanged.
- No cursor/offset is added by the split. The standing
  pagination rule (page to exhaustion, prove total + bounds)
  binds consumers exactly as today; the digest v2 query-path
  fix remains its own owed milestone (see §6).

### 4g. Contract tests (behavior preservation, provable)

Before any split code lands, a NEW system-test module pins
current MCP behavior against a frozen fixture DB (see plan
M0). It captures golden outputs for: drivers default call,
drivers status-filtered, drivers windowed (incl. the undated
exclusion), resources union + kind-filtered, health for
never-run / success / failure kinds, limit-200 truncation
shape, ordering on both tools, and serializer field sets.
The module runs UNCHANGED against the old combined build
and the new serving build; preservation is proven by
byte-identical golden outputs (modulo `scraped_at`/`age`
clocks, which the fixtures freeze), not by re-asserting
intent. If a golden output moves, the split stops — that is
a behavior change wearing a refactor's clothes.

## 5. Deployment shape

| | Before (today) | After (split) |
|---|---|---|
| Serving | `haliffer-school-mcp`, slim-ish image, `school.app serve`, mounts data + grades-cache + deck registry | `haliffer-school-mcp`, slim image, `school.mcp_server.server` entry, mounts store volume ONLY, 127.0.0.1:8765, gateway `/mcp/school/`, token + nginx unchanged |
| Pipeline | `docker exec` into the serving container, every 6h at :15 | `haliffer-school-pipeline` (Chromium image), oneshot `docker run --rm` every 6h at :15 (same schedule, same :15 offset — the index pipeline's :45 and the digest's 18:00 keep their spacing) |
| Timer | `school-scrape.service` (docker exec) + `.timer` | New `school-pipeline.service` (docker run --rm) + same `.timer` schedule; old service removed at cutover, never both armed |
| Store volume | `./data` in deploy-run (DB + profile + vault co-mingled) | SPLIT: store volume (DB only, shared) + pipeline-private volume (profile dir, vault dir — serving cannot see them). Move, don't copy; keep a backup until the post-split probe passes (see §6) |
| Grades-cache | ro mount on serving container | ro mount on pipeline container ONLY |
| Deck registry | ro mount on serving container | ro mount on pipeline container ONLY |
| Restart/rebuild independence | N/A (one container) | Serving rebuild/restart never touches pipeline state or schedule; pipeline rebuild never restarts serving; the DB file is the only shared artifact |
| Rollback | — | Keep the last combined image tagged as fallback through one full timer cycle (6h) post-cutover; rollback = repoint the timer at `docker exec` + restart the combined image. After one clean cycle, delete the fallback tag. |

Gateway surface is untouched by the split: same route
(`/mcp/school/`), same token client (`school-mcp`, paths
`["/mcp/school"]`), same nginx snippet, same
`tokens.json` entry. No auth change, no token rotation.

## 6. Data migration (no dump/reload anywhere)

- **The store moves by remount, not by migration.** The DB
  file IS the store (frozen v1 schema + WAL migration v2).
  Copy nothing row-wise; point both new containers at the
  same file via the store volume, verify row counts match
  pre-split counts, then retire the old mount. The one
  row-wise risk — a scrape racing the remount — is closed
  by stopping the timer during the cutover window and
  running one manual pipeline pass before re-arming.
- **Profile + vault move by directory move** (same host,
  new volume path): `mv` the profile dir and vault dir out
  of the co-mingled data dir into the pipeline-private
  volume; keep the old paths as a backup until the
  post-split probe scrape returns SUCCESS for CLASSROOM_WEB
  (or, pre-birth, until a probe proves the pipeline reads
  the profile path correctly). Serving must fail its build
  if given a profile or vault path — a config-level tripwire
  against re-coupling.
- **HAC cache is upstream, not ours.** The grades MCP owns
  its cache file; the pipeline keeps reading it read-only
  from the same host path. Nothing migrates, nothing is
  re-scraped from HAC by this project.
- **Deck registry moves** (file copy to the pipeline
  config mount; content unchanged; the pipeline build fails
  loud on a malformed registry, as today).
- **In-flight soak data is preserved, not reset.** Run rows,
  merged rows, and `scraped_at` stamps carry over verbatim
  (same file). What the split must NOT do is present a new
  store as if it were the old one — row counts before/after
  are a cutover checkpoint (plan M4).
- **Classroom-active follow-up (post-birth, not split
  scope):** contract fixtures pinned pre-split cover the
  current source set (HAC + web; Classroom inactive). After
  birth activates CLASSROOM_WEB, extend the golden fixtures
  with a Classroom-active variant as a separate milestone —
  the split must not be re-opened to do it.

## 7. Agent boundary (the point of the split)

### Ingestion-side agents — context contains:
`driver_hac/`, `driver_web/`, `driver_classroom_web/`,
`vault/`, `merge/`, `spi/`, `app/scrape_runner.py`, the NEW
pipeline composition root + CLI, pipeline Dockerfile/compose/
timer, deck-registry format, HAC cache contract
(read-only file, mtime-as-`scraped_at`), Classroom session/
profile/vault docs, `deploy/BIRTH.md` (when recovered — see
§10). Plus the interface docs (§4) as read-only ground truth.

### Ingestion-side agents — context EXCLUDES:
`mcp_server/` internals, serializers, auth, gateway/nginx,
tokens, digest logic, personas, tutor, calendar, todo. They
never open the serving container's config or logs to debug
a scrape; they read run rows and the pipeline's own logs.

### Serving-side agents — context contains:
`mcp_server/` (server, services, serializers, auth),
health-evaluation helper, the store as a READ-ONLY view
(repositories as readers + §4 schemas), domain record
shapes, the MCP contract doc (`deploy/README.md`
pagination section), gateway/nginx snippet shape, token
MODEL (client `school-mcp`, path scope — never a token
value). Plus the interface docs (§4) as read-only ground
truth.

### Serving-side agents — context excludes:
`driver_*/`, `vault/`, Playwright/Chromium, deck registry,
HAC cache, scrape timer, profile dirs, merge internals
beyond the §4 policy statement. They never open a driver
to debug a tool response; they read the store and the
contract tests.

### Shared / interface artifacts (both sides read, neither
unilaterally changes):
`domain/` record shapes, `store/schema.py` + §4a–§4c,
the MCP contract (§4f + `deploy/README.md`), the merge
policy statement (§4a), contract-test fixtures + golden
outputs, this design doc, the split plan. An interface
change is a JOINT milestone: both sides' agents (or one
agent explicitly briefed on both sides of that single
change) + dispatcher signoff + contract tests green on
both builds. Side-internal changes need only that side's
gates + contract tests green — no cross-side review.

### What each side's brief must never contain:
the other side's source files (beyond the interface docs),
any secret or token value, the other side's runbooks
(except the §4 interface sections), or any instruction to
"just check the other side" — the interface docs exist so
nobody has to.

## 8. Test migration (gates unchanged, suite split)

Current suite on main: 495 tests collected (unit + system;
M7 closeout recorded 476 unit + 17 system — the plan's M0
milestone re-counts and records the exact split). Gates do
NOT move: coverage ≥90% (fail_under), mypy strict, 500-LOC
cap, system tests gate merges but don't inflate coverage.
What moves is where tests live and what they prove:

| Suite today | After split | Gate |
|---|---|---|
| `tests/unit/driver_*`, `vault`, `merge`, `spi`, `app/test_scrape_runner` | Pipeline suite (runs with pipeline gates) | ≥90% on pipeline code |
| `tests/unit/mcp_server`, `tests/unit/app/test_health` (evaluation part) | Serving suite (runs with serving gates) | ≥90% on serving code |
| `tests/unit/domain`, `tests/unit/store`, `tests/unit/spi/test_registry` (shared parts) | Shared-kernel suite, run by BOTH sides' gates (counted in both — a kernel regression must fail both sides loudly, not one) | ≥90% on kernel code, enforced twice |
| `tests_system/test_end_to_end`, `test_composition_root`, `test_failure_drills`, `test_classroom_*` | Split in two: pipeline system tests (scrape → store assertions, fixture externals, NO MCP) and serving system tests (fixture DB → MCP calls, NO drivers) | Gate merges on their side |
| NEW `tests_system/test_mcp_contract.py` (plan M0) | Contract suite, run against BOTH the old combined build (pre-split pin) and the new serving build (post-split proof) | Byte-identical goldens (modulo frozen clocks); any move stops the split |

`verify.sh` stays ONE script until cutover (all milestones
run the full suite — cheap, ~40s — so no side can break the
other silently). At cutover (plan M4) it splits into
`verify-pipeline.sh` + `verify-serving.sh` + `verify-all.sh`
(joint gate). Per-side scripts are what side agents run
thereafter; `verify-all.sh` is what the dispatcher runs at
signoff.

## 9. Sequencing decision (decisive — governs orchestration)

### Soak / digest v2 cutover — DECISION: split AFTER cutover.

The split is behavior-preserving at the MCP contract, so in
the strict sense it does not invalidate soak evidence — the
digest reads MCP tools, and those tools do not change. But
the soak observes more than the digest: the daily health
watch observes the TIMER mechanism (run cadence, failure
modes, session longevity), and the split replaces exactly
that mechanism (docker-exec into the serving container →
oneshot pipeline container). If a post-split health anomaly
appears mid-soak, attribution muddies: is it the split's
new timer shape, or a real source problem the soak exists
to catch? Worse, cutover tracks ~Oct 29 — days away — so
landing a multi-milestone structural change inside the
soak window means rushing it, then re-baselining the
parallel run if anything looks off (the parallel bar is 5
school days with the final 3 clean; a mid-soak deployment
change arguably restarts that clock). The cost of waiting
is small: after cutover, the split runs against a stable
v2 digest that reads the same frozen contract, so no
digest rework is needed — plus one post-split digest
comparison as a sanity check (not a new soak). The cost
of going before is re-baselined evidence + schedule risk
on a date that is already tight. Sequence: query-path fix
→ cutover → split. The contract-pinning milestone (plan
M0) is the exception: it is docs + tests against the
current build with no production change, so it may start
before cutover on a branch — it makes the post-cutover
split faster, not riskier.

### Profile birth — DECISION: birth FIRST on the current layout, split AFTER.

Birth staging is complete and verified against the current
layout: the Chromium fix, the `:birth` (2.01GB) and
`:birth-ui` (2.03GB) images, the vault dir, the profile
dir, the enroll script, and the classroom env snippet all
exist on hal9k today. Splitting first forces rework of
exactly that staging: the birth-ui image must be re-based
onto the new pipeline Dockerfile and re-verified (headed
Chromium under Xvfb against a new image), the verification
scrape must be re-pointed at the pipeline entry, and the
cutover step changes shape (pipeline image → `:birth`,
serving image slim with no classroom env at all — simpler
in the end, but different from the staged runbook). That
rework is small but it lands on a scarce resource — a
supervised session with Noah + Sam — where a failed
session wastes human time, not just compute. Splitting
after birth costs almost nothing: the profile and vault
are directories, not data to migrate — the split moves
their mounts to the pipeline-private volume (same host
paths, same bytes), then proves it with a post-split
probe scrape (session health probe SUCCESS for
CLASSROOM_WEB). The one real cost of after-birth is
sequencing: birth activates a fourth source, so the
contract fixtures pinned pre-split cover three sources
and need a Classroom-active extension afterward (plan M5,
a follow-up, not a split re-open). Order: birth →
cutover → split. These decisions govern orchestration;
the dispatcher overturns them only on a found flaw, with
the design amended first.

## 10. Open questions (genuinely open, not decided here)

1. **The birth runbook file is missing from the repo.**
   `deploy/BIRTH.md` is referenced by
   `deploy/Dockerfile.birth-ui` ("see deploy/BIRTH.md") and
   was recorded as branch commit `2afb11f`, but that
   object does not exist on hal9k and the branch tip is
   `c090427` — the file was never pushed (or never
   committed). The runbook's substance survives in
   conversation context, but the repo has no runbook. It
   must be recovered or rewritten BEFORE the birth session
   regardless of the split — and the split plan assumes
   the recovered runbook, not the missing file. (Not a
   split-design question; a pre-birth dependency for the
   dispatcher.)
2. **WAL migration versioning.** §4e adds WAL as schema
   migration v2. If the pipeline sets WAL via connection
   pragma instead, migration v2 is unnecessary — the plan's
   M3 milestone decides the mechanism; the design requires
   only the outcome (WAL on, recorded somewhere durable).
3. **Serving health CLI.** §3 retires `school.app health`
   in favor of gateway `get_source_health`. If any caller
   outside the soak watch depends on the CLI's JSON shape
   (sources + evaluation in one document), that caller
   must be found in M2 and the shape preserved as a
   serving-side helper — the plan carries this as an M2
   exit-check question, not an assumption.
4. **Two-repo revisit trigger (§1).** The trigger is a
   second consumer of the stores or serving-driven kernel
   churn. If either appears mid-split, stop and re-decide
   the shape before proceeding — do not finish a one-repo
   split and immediately re-split it.
