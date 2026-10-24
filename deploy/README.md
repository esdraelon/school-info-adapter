# School Information Adapter — deploy runbook

**This directory is artifacts only.** Nothing here was executed
against hal9k during M5; the deploy is a separate, human-checked
step. Steps below are written for whoever runs it (Noah or a later
agent session with deploy approval).

## Artifacts

| File | Purpose |
|---|---|
| `Dockerfile` | python:3.12-slim image; non-root; `python -m school.app serve` |
| `docker-compose.school.yml` | the `school-mcp` service (localhost:8765, read-only grades-cache mount, SQLite + deck-registry volumes) |
| `school-scrape.service` / `.timer` | one scrape pass via `docker exec`, every 6h at :15 |
| `nginx-school.conf.snippet` | `location /mcp/school/` for haliffer.ululuth.com |
| `DIGEST_V2.md` | the digest cutover spec (post-soak step, not part of this deploy) |

## Ordered deploy steps (on hal9k)

1. **Stage the checkout.** Copy the repo to the hal9k workspace and
   create the deploy directory (e.g. `/home/haliffer/school/`) with:
   `docker-compose.school.yml`, a production `deck_registry.json`
   (the M3 discovery format — start from
   `tests/unit/driver_web/fixtures/deck_registry.json`), an empty
   `data/` directory, and a `.env` containing `SCHOOL_MCP_TOKEN`
   (the app's OWN bearer token — generate a fresh random one; never
   commit it). Note: the `data/` directory must be writable by the
   container user (uid 10001) or SQLite cannot create the database.
2. **Build the image.** From the checkout root:
   `docker build -f deploy/Dockerfile -t haliffer-school-mcp .`
3. **Start the service.** From the deploy directory:
   `docker compose -f docker-compose.school.yml up -d` — then
   `docker logs haliffer-school-mcp` should show uvicorn listening on
   8765 with no config errors.
4. **First scrape.** `docker exec haliffer-school-mcp python -m
   school.app scrape` — expect one line per source (hac, team_site,
   agenda) with success/partial outcomes and exit code 0.
5. **Merge the nginx snippet.** Add `nginx-school.conf.snippet`'s
   block to `/etc/nginx/sites-enabled/haliffer.ululuth.com.conf`,
   `nginx -t && systemctl reload nginx`. (No `.bak` files may remain
   inside sites-enabled.)
6. **Register the token with the gateway auth service.** The
   gateway's `auth_request` layer checks path-scoped tokens from
   `/var/www/haliffer/auth/tokens.json` (sha256(token) ->
   {"client", "paths"}). Add an entry for the SCHOOL_MCP_TOKEN:
   client `school-mcp`, paths `["/mcp/school"]`. Caveat: the file is
   bind-mounted into the `haliffer-auth` container as a single file —
   an atomic-rename replace swaps the inode and the container keeps
   reading the old one; edit in place, or restart `haliffer-auth`
   after replacing.
7. **Install the scrape timer.** Copy `school-scrape.service` and
   `school-scrape.timer` to `/etc/systemd/system/`,
   `systemctl daemon-reload && systemctl enable --now
   school-scrape.timer`; confirm with `systemctl list-timers`.
8. **Smoke checks.**
   - MCP health through the gateway: `get_source_health` via
     `https://haliffer.ululuth.com/mcp/school/` with the
     SCHOOL_MCP_TOKEN as the Bearer
     — hac/team_site/agenda report SUCCESS with fresh finish stamps.
   - CLI health inside the container: `docker exec
     haliffer-school-mcp python -m school.app health` exits 0 and its
     evaluation reports `ok: true`.
   - One digest-input pass: `get_drivers {"student_id": "sam"}`
     returns merged records with `scraped_at` set.
9. **Soak, then cut the digest over** per `DIGEST_V2.md`. The grades
   MCP (digest v1) stays up throughout; rollback is a digest-config
   change, nothing here is destructive.

## MCP collection contract — pagination & coverage (audit 2026-10-24)

Every MCP tool returning a collection MUST document its default
limit, its ordering, and how a consumer pages to exhaustion. This
section is that document for `get_drivers` / `get_resources`.
It exists because of a real incident: on 2026-10-23 a default
`get_drivers` call returned exactly 200 items, oldest-first, and a
Science Unit Assessment due 2026-10-23 was invisible until the
query was windowed by due date. A capped list presented as complete
is a data-corruption bug, not a display detail.

### `get_drivers` (server.py, services.py, repositories.py)

- **Default limit: 200.** No `cursor` / `offset` parameter exists.
  The limit is applied in `QueryServices.list_drivers` AFTER the
  status and due-window filters (`records[:max(limit, 0)]`), so a
  windowed call can still be capped at 200 inside its window.
- **Ordering:** `ORDER BY due_at IS NULL, due_at, title` — dated
  items oldest-first, undated items last. A default call therefore
  truncates the NEWEST items first.
- **Due-window params** (`due_on_or_after` / `due_on_or_before`,
  ISO-8601): a window EXCLUDES undated items entirely — an item
  with no due date cannot be shown to fall inside a window. Any
  consumer paging by window must ALSO make an unwindowed (or
  per-course) call for the undated set, or undated items are
  silently dropped.
- **Truncation is silent.** The response is a bare list: no total
  count, no `has_more` flag. A page of exactly 200 is
  indistinguishable from a complete result of exactly 200 — treat
  it as possibly-truncated, never as complete.

### `get_resources`

- **Default limit: 200.** No `cursor` / `offset` parameter exists.
  The limit is applied AFTER the visibility, course, and kind
  filters.
- **Ordering:** `ORDER BY title` (alphabetical, SQLite collation).
  A default call truncates the tail of the alphabet first.
- **No time-window params exist** on this tool. Paging to
  exhaustion must partition on `course_name` / `kind` / `student_id`
  (or pass an explicit large `limit` as one leg of a cross-check —
  never as the coverage proof on its own, since the response
  carries no total to check against).
- **Course-filter union semantics:** a `course_name` filter matches
  records with no course as well as records of that course
  (courseless material is shared). Partition counts therefore
  double-count courseless rows — the union must be deduplicated
  (by `student` + `url`) before it is compared to any total.
- **Unknown arguments are ignored, not rejected.** A call with
  `{"student": "sam"}` (the wrong parameter name — the tool takes
  `student_id`) returns the unscoped, default-limited list with no
  error. Consumers must use `student_id`; a misspelled argument
  fails silently.

### How a consumer pages to exhaustion (no cursor exists)

1. Partition the query so every single call provably returns
   fewer than the limit: drivers by due window AND course (narrow
   a window until its count is < 200); resources by course × kind.
2. Deduplicate the union (drivers by dedup key; resources by
   `student` + `url`).
3. Prove coverage: total count AND bounds. Drivers: min/max
   `due_at` across the union, plus the undated count from the
   unwindowed leg. Resources: the partition-union count checked
   against an independent leg (e.g. an explicit-large-limit call).
4. If any partition returns exactly 200, subdivide it — that
   partition is not proven complete.

### Audit evidence (live gateway probe, 2026-10-24)

- `get_drivers {"student_id": "sam"}` (default): 200 items, last
  due 2026-09-22 ("Chemistry Unit Test"). True total with
  `limit: 100000`: **562**, all dated, due range 2026-04-26 →
  2027-04-23 (ASSIGNED 459, GRADED 90, MISSING 13). Monthly due
  windows Aug 2026 – Jun 2027 sum to only 535: 27 drivers are due
  Apr–May 2026, so a school-year window does NOT exhaust drivers
  either — windows must span the observed min/max, not assumed
  school-year bounds.
- `get_resources` (default): 200 rows. True total with
  `limit: 100000`: **398 rows / 197 distinct URLs** (title-order
  confirmed). The school-index M1 harvest consumed the default
  call and therefore harvested a truncated set — see
  `docs/HARVEST-PAGING.md` in the school-index repo for the
  harvest-side requirement and verdict.

### Standing consumer rule

No consumer anywhere in the estate may rely on a single
default-limit call for coverage: not the harvester, not the digest
(DIGEST_V2.md's unwindowed `get_resources` call and its unwindowed
`status: "MISSING"` call are both single-default-limit calls and
need the paging fix before cutover reliance), not soak
comparisons, not benchmark censuses. A coverage claim without a
total + bounds proof is a gate failure.
