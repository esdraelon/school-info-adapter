# Soak plan — school adapter, first 1–2 weeks after deploy

**Window:** from the deploy (see `deploy/README.md`) until the exit
criteria below hold — expect 1–2 weeks of school days. During the
whole soak, **digest v1 (grades MCP) remains the delivered digest**;
the school adapter is under observation, not yet trusted. Nothing in
this plan is destructive, and every step is reversible by stopping
the school MCP (v1 does not depend on it).

## Daily watch (2 minutes)

Run `python -m school.app health` (or `get_source_health` through the
gateway) once a day and note, per source kind:

- **Latest outcome** (SUCCESS / PARTIAL / FAILURE) and finish age.
- Expected rhythm: a scrape pass every 6h (the deploy timer); the HAC
  cache itself refreshes on the grades scraper's school-day cadence.
- Any **FAILURE** gets a same-day look at its run message — the
  message is written to be the diagnosis (dead URL, missing cache,
  unparseable deck, expired classroom session).
- **HAC age vs reality:** the health verdict goes UNHEALTHY-stale at
  30h. Confirm the banner behavior matches the grades scraper's true
  cadence (e.g. a quiet weekend should not read as an incident if the
  scraper legitimately skips weekends — if it pages, the threshold,
  not the data, is what changes).

## What to watch over the window

1. **PARTIAL frequency per kind, and its causes.**
   - *HAC PARTIAL* = cache items the mapping layer could not map;
     the run message counts and names them. A rising skip count is
     cache-shape drift — capture an example item before it scrolls
     away.
   - *AGENDA PARTIAL* = a deck failed (fetch, unknown template, no
     parseable week, or — since M7 — a parser exception, which now
     fails only that deck). Recurring PARTIAL on the same deck is
     format drift (drill D2's shape): recapture that deck's fixture
     and diff its slide structure.
   - *CLASSROOM_WEB PARTIAL* names the failed tab(s). Repeated tab
     failures with a healthy session are extraction drift against
     the live To-do page.
2. **Merge spot-checks (2–3× per week).** Pick 3–5 items and compare
   the merged `get_drivers` view against ground truth in both
   sources directly:
   - an item present in **both HAC and Classroom** — the merged
     record must carry Classroom's status/score (precedence
     CLASSROOM_WEB > HAC) and the freshest `scraped_at`;
   - an item **only in Classroom** (HAC's blind spot — the case that
     motivated v2) — it must appear in the merged view at all;
   - an **agenda-only** item (earliest warning) — present, ASSIGNED,
     with the deck's due date (watch FD overrides).
   Log any mismatch with the item title and both sources' raw views.
3. **Staleness honesty.** On a day the grades scraper is known to
   have lagged, confirm the digest-facing health reads stale/failed
   rather than fresh (drill D3's shape), and that record-level
   `scraped_at` still shows the cache's real mtime.
4. **Classroom session longevity.** Record the date of the first
   `session expired; supervised re-login needed` FAILURE. That
   interval is the supervised re-login cadence — a soak *output*,
   used to set expectations, not a failure of the soak. After each
   supervised re-login, confirm the next pass returns SUCCESS.
5. **Vault/re-auth behavior.** Any `credential vault error` run
   message means boot-time verify passed but a read failed — treat
   as an incident (it should be impossible; D4 covers the swapped-key
   case at boot).

## Digest v1/v2 parallel run

For **5 school days** (within the soak window), draft digest v2
alongside v1 every day, per `deploy/DIGEST_V2.md`:

- v1 is delivered as today; v2 is drafted, compared, and discarded.
- Compare: the missing list (count + membership), due-tomorrow
  items, and the staleness banner (present exactly when v2's health
  evaluation says a source is UNHEALTHY/warning-worthy).
- Every discrepancy is logged with its cause class: HAC lag (v1
  wrong, v2 right), merge error (v2 wrong — soak-blocking), source
  outage (both degrade; v2 must *say* so), or cosmetic.
- Cutover is justified when the final **3 consecutive** comparison
  days show v2 matching or beating v1 on every compared item, with
  zero merge errors in that stretch.

## Exit criteria (all must hold)

1. ≥ 10 school days soaked, including at least one full week with
   the classroom driver configured (or an explicit decision to soak
   classroom separately).
2. No unexplained FAILURE in the final week; every FAILURE in the
   window has a named cause and, where fixable, a fix.
3. PARTIAL causes per kind are understood and stable (no rising
   trend in HAC skip counts or deck failures).
4. The final 3 merge spot-checks are clean.
5. The digest parallel run meets its cutover bar above.
6. If a classroom session expiry occurred, the supervised re-login
   path was exercised successfully at least once.

Meeting the criteria starts
[`LEGACY-DECOMMISSION.md`](LEGACY-DECOMMISSION.md). Missing any one
extends the soak — the plan is the deliverable, not the date.
