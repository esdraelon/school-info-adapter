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
   (the gateway bearer token value; never commit it).
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
6. **Install the scrape timer.** Copy `school-scrape.service` and
   `school-scrape.timer` to `/etc/systemd/system/`,
   `systemctl daemon-reload && systemctl enable --now
   school-scrape.timer`; confirm with `systemctl list-timers`.
7. **Smoke checks.**
   - MCP health through the gateway: `get_source_health` via
     `https://haliffer.ululuth.com/mcp/school/` with the bearer token
     — hac/team_site/agenda report SUCCESS with fresh finish stamps.
   - CLI health inside the container: `docker exec
     haliffer-school-mcp python -m school.app health` exits 0 and its
     evaluation reports `ok: true`.
   - One digest-input pass: `get_drivers {"student_id": "sam"}`
     returns merged records with `scraped_at` set.
8. **Soak, then cut the digest over** per `DIGEST_V2.md`. The grades
   MCP (digest v1) stays up throughout; rollback is a digest-config
   change, nothing here is destructive.
