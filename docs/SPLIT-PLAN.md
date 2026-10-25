# Split plan — school-info-adapter: ingestion pipeline × serving MCP

**Companion:** `docs/SPLIT-DESIGN.md` (target architecture +
interface). This plan sequences the work; the design doc is the
authority on what the interface is. **Sequencing is decided**
(design §9: birth first on the current layout, digest v2
cutover, then split) and governs orchestration on dispatcher
review of this design + plan; the dispatcher overturns it
only on a found flaw, amending the design first. The M0
contract-pinning milestone is the only exception to the
ordering: it changes no production behavior and may start
before cutover, on a branch.

## Rules for every milestone (non-negotiable)

- **One agent per milestone, tight context.** Each agent gets
  its milestone spec + the design doc's interface section (§4)
  + its allowed directories — nothing else. An agent that
  needs the other side's context has found an interface gap:
  stop and report it, don't go read the other side.
- **No milestone bundles ingestion + serving feature work.**
  The split IS the boundary. The single exception is M3, an
  explicitly joint interface milestone with both sides' gates.
- **Stacked branches, fast-forward only, dispatcher verifies.**
  Each milestone branches off the previous milestone's merged
  tip; no branch merges until its checklist is green AND the
  dispatcher has re-run its gates independently. No force
  operations, ever.
- **Tests stay green throughout.** Until M4, every milestone
  runs the FULL `verify.sh` (coverage ≥90%, mypy strict,
  500-LOC cap, system tests). A red gate stops the milestone;
  a milestone that breaks the other side's tests has failed
  its own exit criteria, even if its own tests pass.
- **Docs only here; code milestones are separate dispatches.**
  This plan authorizes no code by itself — each milestone
  below is dispatched with its own brief when its entry
  criteria hold.

## Milestone M-1 — Design docs to main (dispatcher-owned, docs only)

- **Work:** merge `stack/split-design` (SPLIT-DESIGN.md +
  SPLIT-PLAN.md) to main after dispatcher review.
- **Entry criteria:** dispatcher has reviewed the design
  for interface completeness and the sequencing decisions.
- **Exit criteria:** docs on main; branch tip recorded;
  code milestones unblocked in sequence below.
- **Gates:** docs-only; review only (no test gate — no code
  changed). If the dispatcher finds a flaw, the design doc
  is amended FIRST (new commit on main), then milestones
  proceed on the amended design.

## Milestone M0 — Contract pinning (tests only, no production change)

- **Work:** new `tests_system/test_mcp_contract.py`: frozen
  fixture DB (fixed rows across HAC + TEAM_SITE + AGENDA;
  Classroom inactive) + golden outputs for the calls in
  design §4g (drivers default / status-filtered / windowed
  incl. undated exclusion; resources union + kind-filtered;
  health never-run / success / failure; limit-200 shape;
  ordering on both tools; serializer field sets). Clocks
  frozen in fixtures so goldens are byte-comparable.
- **Allowed context:** `tests_system/`, `tests/unit/`
  (fixtures only), `mcp_server/serializers.py` (read-only),
  design §4f–§4g, `deploy/README.md` pagination section.
- **Excluded context:** `driver_*/`, `vault/`, deploy
  runbooks, gateway config, digest code.
- **Entry criteria:** M-1 done. Main green (full verify.sh).
- **Exit criteria:** contract suite passes against the
  CURRENT combined build; golden files committed; exact
  test counts recorded (unit + system + contract, replacing
  the stale M7 figures wherever they appear in this plan's
  descendants).
- **Gates:** full verify.sh green (the new suite included).
- **Notes:** may start before cutover (branch only, nothing
  deployed). This milestone is what makes "behavior
  preservation" provable instead of asserted.

## Milestone M1 — Pipeline composition root + entry (ingestion side)

- **Work:** new pipeline-only composition root (registry +
  `ScrapeRunner`, no MCP/token/uvicorn) + `school.pipeline`
  CLI entry (`pipeline scrape`). Pipeline config subset per
  design §2. Old `school.app scrape` path STAYS (additive
  milestone — no deletions). A system test proves the new
  entry produces row-identical store content to the old
  path on fixture externals.
- **Allowed context:** `driver_*/`, `vault/`, `merge/`,
  `spi/`, `app/scrape_runner.py`, `app/config.py` (pipeline
  subset), `domain/`, `store/` (interface §4 only),
  pipeline Dockerfile/compose/timer drafts (not deployed).
- **Excluded context:** `mcp_server/` (except reading §4f
  for what must not change), serializers, auth, gateway,
  digest, personas, serving deploy files.
- **Entry criteria:** M0 merged. M0 goldens green.
- **Exit criteria:** pipeline entry runs a full fixture
  scrape with row-identical output to the old path;
  pipeline config documented; no serving file modified
  (prove with `git diff --stat` showing only pipeline-side
  paths + tests).
- **Gates:** full verify.sh green; row-identity test green.

## Milestone M2 — Serving standalone deployables (serving side)

- **Work:** slim serving image + compose file around the
  EXISTING `mcp_server/server.py:main` entry (no new serving
  code except build/deploy files); serving config documented
  (port/token/DB path/thresholds); a build-time check that
  fails the serving image if a browser binary is present;
  a system test proving serving moves no rows (fixture DB
  checksums before/after every tool call). Answer the
  design §10.3 question in the milestone report: does any
  caller outside the soak watch depend on the `school.app
  health` JSON shape? If yes, preserve it as a serving-side
  helper in this milestone (not later).
- **Allowed context:** `mcp_server/`, `app/health.py`
  (evaluation helper only), `domain/` record shapes,
  `store/` (read-only view + §4), deploy README + nginx
  snippet shape, design §3–§4.
- **Excluded context:** `driver_*/`, `vault/`,
  Playwright/Chromium, deck registry, HAC cache, scrape
  timer, profile dirs, merge internals beyond §4a.
- **Entry criteria:** M1 merged (order matters only for
  branch stacking, not for content — M2 must not import
  M1's code; it must not need to).
- **Exit criteria:** serving image builds slim; serving
  entry serves the contract suite's golden calls from a
  fixture DB with byte-identical outputs to M0 goldens;
  no-read-only-proof test green; health-CLI question
  answered with evidence; no ingestion file modified
  (`git diff --stat` proof as in M1).
- **Gates:** full verify.sh green; contract suite green
  against the new serving entry (not just the old build).

## Milestone M3 — Interface hardening: WAL + ACLs (JOINT milestone)

- **Work:** WAL mode on the store (design §4e: migration v2
  OR connection pragma — milestone decides the mechanism,
  design requires only the outcome, recorded durably);
  store-volume ACL plan documented (serving uid 10001 rw on
  DB + sidecars via default ACLs); serving read-only proof
  test promoted to a permanent merge gate; a two-process
  coexistence test (pipeline write + serving read
  interleaved, incl. a WAL-sidecar ownership swap) proving
  no "readonly database" failure in either direction.
- **Allowed context:** `store/` (schema + repositories),
  `domain/`, design §4d–§4e, index deploy/README WAL/ACL
  section (reference model). This is the ONE milestone
  whose context spans both sides — because the interface
  itself is the work.
- **Excluded context:** all driver internals, all serving
  handler logic, vault contents, tokens, deploy secrets.
- **Entry criteria:** M2 merged. M0 goldens green on both
  builds.
- **Exit criteria:** WAL on (proven by `PRAGMA
  journal_mode` readback on a fixture copy); coexistence
  test green; ACL plan written into the deploy runbook
  draft (not applied — application is M4); no behavior
  change (contract suite green on both builds).
- **Gates:** full verify.sh green; contract suite green
  on both builds; coexistence test green.

## Milestone M4 — Cutover (deployment milestone, dispatcher-checked)

- **Work:** deploy the split: new `school-pipeline`
  service (oneshot `docker run --rm`, same :15 schedule)
  + slim `haliffer-school-mcp` serving image; retire
  `docker exec` timer + old combined image path; delete
  the retired `school.app serve|scrape|health` entry
  paths (serving → `server.main`, scrape → pipeline
  entry, health → gateway tool); split the store volume
  from the pipeline-private volume (profile + vault move
  by directory move, backup kept until the probe passes);
  update `deploy/README.md` + `deploy/Dockerfile` +
  compose + timer units; split `verify.sh` into
  `verify-pipeline.sh` + `verify-serving.sh` +
  `verify-all.sh` (joint gate).
- **Allowed context:** everything M1–M3 produced + deploy/
  + live deploy-run layout (names + mounts only — never
  `.env` contents, never tokens).
- **Excluded context:** digest config, soak scripts,
  persona/tutor/calendar code (untouched by cutover).
- **Entry criteria:** M3 merged; sequencing decided per
  design §9 (split after cutover + after birth); digest v2
  cutover complete; profile birth complete. Any of these
  is overturned only by a dispatcher-found flaw with the
  design amended first — never by milestone-agent
  improvisation. Timer stopped for the cutover window
  (dispatcher-owned step).
- **Exit criteria (ALL must hold, dispatcher-verified):**
  serving container = slim image, mounts store volume
  ONLY; pipeline timer armed at :15, oneshot shape;
  gateway smokes green (all three tools; Pythagorean
  passage in top 3); row counts before/after identical;
  post-split probe scrape SUCCESS (CLASSROOM_WEB probe
  when configured, else a web-source probe proving the
  pipeline path); one digest comparison sanity check
  (v2 output vs pre-split v2 output — no new soak, just
  a same-day comparison); old combined image tagged as
  fallback through one full timer cycle (6h), then tag
  deleted; old timer service removed (never both armed).
- **Gates:** `verify-all.sh` green; contract suite green
  against LIVE serving (gateway, not fixture); smokes
  green; row-count checkpoint signed by the dispatcher.
- **Rollback:** repoint timer at `docker exec` + restart
  combined image (fallback tag); serving slim image kept
  but not serving. Rollback is a dispatcher decision, not
  an agent improvisation.

## Milestone M5 — Post-birth follow-up (NOT split scope; sequenced here)

- **Work:** extend contract fixtures with a
  Classroom-active variant (fourth source live, precedence
  exercised end-to-end) + re-run goldens; confirm the
  split needed no change for the fourth source (if it did,
  that is a new design amendment, not a silent fix).
- **Entry criteria:** M4 done + profile birth complete.
- **Exit criteria:** Classroom-active goldens green on
  live serving; design §6 follow-up note closed.
- **Gates:** `verify-all.sh` green; contract suite green
  (both variants).

## Branch strategy + signoff

- Branches stack: `stack/split-m0-contract` →
  `stack/split-m1-pipeline` → `stack/split-m2-serving` →
  `stack/split-m3-interface` → `stack/split-m4-cutover`.
  Each merges to main ONLY after its checklist is green
  AND the dispatcher re-runs its gates independently.
  M5 branches off post-cutover main when birth lands.
- Signoff for the whole split (dispatcher, after M4):
  serving container mounts store volume only (inspect);
  pipeline timer oneshot shape (unit file); contract
  suite green on live serving; `verify-all.sh` green;
  row counts match; fallback tag deleted after one clean
  cycle; CONTEXT.md + assistant ARCHITECTURE.md updated
  to the split shape (dispatcher-owned docs touch — the
  assistant repo is NOT in this plan's milestones).
- Pre-existing dependencies outside this plan (dispatcher/
  Noah owned, NOT split milestones): birth-checkout fast-forward
  (design §10.1 — hal9k checkout to the `stack/birth-staging` tip
  before the birth session, split or no split);
  digest v2 query-path fix (before cutover reliance);
  digest v2 cutover itself; profile birth session.
