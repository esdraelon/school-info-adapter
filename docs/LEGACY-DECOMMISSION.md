# Decommission plan — the legacy grades-only path

**Status: plan only.** Nothing here is executed by the milestone that
wrote it. Execution starts only after the soak exit criteria
([`SOAK-PLAN.md`](SOAK-PLAN.md)) are met and the digest is fully on
the school MCP.

## The dependency, stated precisely

The school adapter's HAC driver does **not** talk to the grades MCP
server. It reads a **file**: the grades cache at `SCHOOL_HAC_CACHE_PATH`
(on hal9k, `/var/www/haliffer/grades-cache/cache/sam_classwork_cache.json`),
written and refreshed by the grades **scraper**. Two different things
share the "grades" name:

- **The scraper / cache producer** — writes the cache file. The
  school adapter depends on it completely: no fresh cache, no HAC
  data (health goes UNHEALTHY-stale, drill D3). **Not a decommission
  candidate. Ever, while the adapter consumes this cache.**
- **The MCP serving layer** — the `haliffer-grades-mcp` container
  serving tools at the gateway's `/mcp/grades/` route, reading the
  same cache file for its own responses. Digest v1 is its consumer.
  **This** is the decommission candidate, and only once nothing
  consumes it.

Corollary: before touching the container, establish where the
scraper actually runs (its own process/cron, or inside the
`haliffer-grades-mcp` container). If it lives inside that container,
split it out first — run the scraper standalone against the same
cache directory — and watch the cache mtime advance for 2 school
days before proceeding. Do not guess the topology; check it.

## Preconditions

1. Soak exit criteria met (SOAK-PLAN.md).
2. Digest runs entirely on the school MCP (`/mcp/school/`); v1's
   digest configuration is archived, not deleted.
3. The scraper-topology question above is answered in writing (a
   line in the execution log: where the cache writer runs).

## Order of operations

Each step is independently reversible; do not batch them.

1. **Drain the consumers.** Enumerate everything that calls
   `/mcp/grades/` (digest v1 config, any other routines/jobs).
   Switch each to `/mcp/school/` or retire it explicitly. Then watch
   the gateway logs for **≥ 3 school days**: zero `/mcp/grades/`
   calls. *Rollback: none needed — nothing changed yet.*
2. **Remove the route.** Delete the `/mcp/grades/` location block
   from the haliffer gateway nginx config; `nginx -t && systemctl
   reload nginx`. The grades MCP still runs; it is simply no longer
   reachable. *Rollback: restore the block, reload.*
3. **Stop the serving layer.** Stop (do not delete) the
   `haliffer-grades-mcp` container. For the following **2 school
   days**, watch the school adapter's HAC health: runs must stay
   SUCCESS and the cache mtime must keep advancing — that is the
   proof the scraper is independent of what was stopped. If HAC goes
   stale, the scraper was coupled to the container: restart it,
   return to the topology step, split the scraper out properly.
   *Rollback: start the container, restore the route.*
4. **Remove the serving layer.** Delete the container and its
   compose/service definitions; archive the configuration with the
   execution log. Keep the gateway config history (the removed block
   is one reload away if ever needed again).
5. **Final sweep.** Confirm: `/mcp/grades/` returns 404 at the
   gateway, the school adapter's health is all-OK, and
   `docs`/deploy notes no longer reference the grades MCP as a live
   serving path (it remains, correctly, as the cache's producer).

## What must NOT be removed

- **The grades scraper / cache producer** — whatever process refreshes
  `/var/www/haliffer/grades-cache/` (see the dependency above).
- **The cache directory and files** — the school adapter's HAC input,
  per student (`sam_classwork_cache.json` today; future students'
  caches follow the same rule).
- **Any credentials the scraper itself uses** to reach HAC.
- **The school adapter** and its store — obviously, but note the
  asymmetry: the school store *can* be discarded without harming any
  legacy path (it rebuilds from sources); the cache file cannot.

## Out of scope

- Changing how the scraper works or what it writes. If the adapter
  ever grows its own HAC fetching, that is a new milestone with its
  own plan — this document retires a serving layer, not a scraper.
- The todo MCP and other gateway routes: untouched by every step.
