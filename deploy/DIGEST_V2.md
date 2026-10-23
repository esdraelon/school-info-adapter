# Digest v2 — cutover spec for the daily Sam school digest

**Status:** spec only (M5). The digest job itself is NOT changed by
this milestone; cutover happens after the school MCP is deployed and
soaked (see the deploy README runbook).

## Why v2

Digest v1 reads the grades MCP directly: a missing-list whose counts
are only as good as HAC's batch-posting habits. V2 reads the school
adapter's merged store instead — HAC sightings merged with the agenda
decks (earliest warning) and the team site — and, critically, it reads
*provenance with the data* (C10): every record carries `scraped_at`,
and every source reports its latest run. When the data is old, the
digest says so up front instead of presenting a merged fiction (the
HAC-lag lesson from 2026-10: never sound urgency off stale counts).

## Data flow

Against `https://haliffer.ululuth.com/mcp/school/` (bearer <redacted>):

1. **Health first.** Call `get_source_health` (no arguments). Evaluate
   each source with the same rules as `school.app.health`:
   UNHEALTHY when it never ran, its latest outcome is `FAILURE`, or
   its age exceeds the threshold (HAC 30h; TEAM_SITE/AGENDA 192h);
   `PARTIAL` is a warning.
2. **Staleness banner.** If any source is UNHEALTHY (or a warning the
   author chooses to surface), the digest LEADS with a banner naming
   the source and its last successful scrape, e.g.:

   > ⚠ HAC data is stale — last successful scrape finished
   > 2026-10-21T18:00:00Z (31h ago); counts below may lag reality.

   A source whose latest outcome is FAILURE is named as failed (with
   its run message) rather than "stale": never-running sources are
   named as never having run.
3. **Due/missing windows.** Call `get_drivers` for the student with
   explicit due windows (ISO-8601 bounds; undated items are excluded
   by any window — ask separately, unwindowed, for those):
   - overdue + missing: `status: "MISSING"`, no due bound;
   - due tomorrow: `due_on_or_after` / `due_on_or_before` spanning
     the school day;
   - the week ahead: window through Sunday.
4. **Resources for what's due.** Call `get_resources` for the student
   (optionally per course / `kind: "NOTES" | "TUTORIAL"`) and attach
   the matching notes links and tutorial sessions to the due items —
   v2's headline improvement over a bare missing list.

## Tool-call shapes (verified by the M4 system test)

```jsonc
// Health — no arguments
{ "tool": "get_source_health", "arguments": {} }
// -> [{ "source_kind": "HAC", "last_outcome": "SUCCESS",
//       "last_finished_at": "2026-10-22T18:00:00Z",
//       "age_seconds": 1800.0, "message": null }, ...]
// A never-run source reports null outcome/finished/age.

// Due items, merged across sources
{ "tool": "get_drivers", "arguments": { "student_id": "sam" } }
{ "tool": "get_drivers",
  "arguments": { "student_id": "sam", "status": "MISSING" } }
{ "tool": "get_drivers",
  "arguments": { "student_id": "sam",
                 "due_on_or_after": "2026-10-23T00:00:00Z",
                 "due_on_or_before": "2026-10-23T23:59:59Z" } }
// -> records like:
// { "title": "Quiz: Pythagorean Theorem", "kind": "QUIZ",
//   "status": "ASSIGNED", "source_kind": "AGENDA",
//   "due_at": "2026-10-26T05:00:00Z", "score_text": null,
//   "course": { "name": "Math 8", ... }, "scraped_at": "..." }
// GRADED items from HAC carry "score_text": "87.77" verbatim.

// Resources (student's own + shared)
{ "tool": "get_resources",
  "arguments": { "student_id": "sam", "kind": "NOTES" } }
{ "tool": "get_resources", "arguments": { "student_id": "sam" } }
```

Enum values on the wire are domain names: statuses `ASSIGNED`,
`MISSING`, `DONE`, `GRADED`; kinds `NOTES`, `TUTORIAL`, `LINK`,
`DOCUMENT`; sources `HAC`, `TEAM_SITE`, `AGENDA`, `CLASSROOM_WEB`.

## Rollback

Digest v1 (the grades MCP tools at `/mcp/grades/`) remains deployed
and untouched by this cutover — the grades MCP keeps scraping its
cache exactly as today (the school adapter *reads* that cache; it
does not replace the scraper). Rolling back is a digest-config change
back to the v1 tools; no data migration is involved in either
direction, and the school store can be discarded without affecting v1.
