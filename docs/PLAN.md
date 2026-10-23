# School Information Adapter — Plan

**Date:** 2026-10-22 · **Status:** Draft for Noah's review · **Host:** hal9k (system of record)
**Vision (Noah):** extend/wrap the HAC MCP into a general school-information adapter on hal9k, built from pluggable **drivers** (source adapters) that feed two normalized concepts:

- **Drivers (due items)** — anything with a date: what it is, which class, when it's due.
- **Resources** — notes, tutorial sessions, answer keys, links: the material that helps get the drivers done.

Written for Sam first; the pattern should generalize to Aiden/Owen later.

---

## 1. What we already have

- **HAC MCP** (`haliffer-grades-mcp` on hal9k, `/mcp/grades/` on the gateway): 10 read-only tools — missing assignments, due today/week, low grades — backed by a scrape cache at `/var/www/haliffer/grades-cache`. It works, but it only sees what teachers have entered in the gradebook, and it lags reality (the 14-vs-2 missing-count gap). It has no concept of upcoming work that isn't in HAC yet, no materials, and no scrape timestamps on its cache (freshness is inferred, not proven).
- **The digest** (daily 18:00) consumes the HAC MCP. It gets much better the moment drivers + resources exist.

## 2. Source inventory (verified 2026-10-22)

| Source | Access | Yields | Verified |
|---|---|---|---|
| HAC (existing) | Scrape, cached | Grades, missing, due items (laggy) | ✅ running |
| 8th Grade Team Site | **Public**, no login | Hub linking everything below | ✅ fetched |
| Math Weekly Agenda (Slides, published) | **Public** | Per-week Mon–Fri grids: topics, **practice + due dates**, notes/key links | ✅ rendered + transcribed |
| Tutorial Schedule (linked from team site) | Public (presumed) | Tutorial sessions → Resources | ⏳ not yet parsed |
| Team-site Google Doc / Sheet / Drive files | Public (presumed) | TBD by inspection | ⏳ |
| Google Classroom — guardian summaries | Parent's own Gmail | Missing / upcoming / class activity | ❌ **DEAD — LISD has guardian access disabled district-wide (family was told directly, 2026-10-22)** |
| Google Classroom — class calendars | ICS or share-to-parent | Due dates only | ❌ **DEAD — verified 2026-10-22:** the class calendars are district-owned read-only subscriptions in Sam's account ("Settings for other calendars"). The Math calendar's settings show **no secret ICS address** and **no sharing controls at all** (permissions: "Anyone can: See nothing / You can: See event details"). Sam owns nothing shareable here. |
| Google Classroom — official API | OAuth as Sam | Everything, cleanest model | ❌ **DEAD — verified 2026-10-22:** consent blocked at the account level: *"Access blocked: Your institution's admin needs to review Haliffer Classroom Test"* (Error 400: access_not_configured), in Testing AND after publishing to Production. The GCP project + app exist and are In production under Noah's account (`haliffer-classroom-test`, client `515166860443-...`) with branding hosted at haliffer.ululuth.com/classroom/ — parked in case LISD ever allowlists the client ID. Reviving it requires a district Workspace admin, nothing on our side. |
| Google Classroom — web UI (To-do page) | Browser session as Sam | Full fidelity: status, missing, materials | ✅ **login verified 2026-10-22** (see below); unattended persistence still ◐ |

**Login test (2026-10-22, Haliffer's browser, Noah + Sam present):** signed in as Samuel Smith (`samuel.smith26@k12.leanderisd.org`) with **no verification challenge and no district block**. 9 classes visible (Passwater SS, Gholizadeh Science, VanDyke Math 8, Goodie ELA, Williamson Wind Ensemble, Johnstone Band, Justice Choir, Counseling Corner, Library). To-do page renders fully: Assigned 4 this week (2 due that night), Missing 16 total (3 this week), Done 131. Classroom surfaced a due-tonight Science item HAC had not flagged — the enrichment value is real. This session lives in Haliffer's browser profile, not on hal9k; the hal9k profile birth remains a Stage 0/3 step.

The agenda deck deserves a note: slide content renders as **images**, but each text block carries its content as **alt text** in the published HTML and the hyperlinks (notes, keys) are live elements. So extraction is a plain HTTP fetch + alt-text parse — **no OCR and no browser needed**. Cross-check: the deck's Pythagorean quiz (Fri 10/16, retake final day 10/26, workbook completion check) matches HAC exactly — the two sources corroborate each other, which is what makes merging them valuable (HAC says what's *graded/missing*; the agenda says what's *coming*, before any teacher touches the gradebook).

Decks are per-subject (this one is Math). The team site links the set; expect one agenda deck per core class, same template.

## 3. Architecture

```
                        hal9k (system of record)
 ┌──────────────────────────────────────────────────────────────┐
 │  school-mcp  (extends/wraps haliffer-grades-mcp)           │
 │  /mcp/school/ on haliffer.ululuth.com, bearer auth,        │
 │  runs as dave.bot — same pattern as todo/grades/datenight  │
 │                                                            │
 │  Normalized store (SQLite): drivers, resources,            │
 │  driver_runs (per-source scrape log: started, ok,          │
 │  records_seen, error)                                      │
 │                                                            │
 │  Source drivers (scheduled scrapers, systemd timers):      │
 │   ├─ hac          (wrap existing cache/tools)              │
 │   ├─ team-site    (hub crawl → discovers decks/docs)       │
 │   ├─ agenda       (Slides pub-HTML alt-text parser)        │
 │   ├─ guardian     (parse Noah's Gmail summaries)           │
 │   ├─ gcal         (ICS poll / shared class calendars)      │
 │   ├─ classroom-api (only if the consent test passes)       │
 │   └─ classroom-web (Playwright, persistent profile)        │
 └──────────────────────────────────────────────────────────────┘
```

Design rules carried over from the existing stack: hal9k holds tools + data, Haliffer is the pacing layer; one gateway hostname, path-routed MCPs; each driver fails independently and **says so** (see §5).

### Data model

**Driver (due item)**
`student, course/class, title, kind (assignment|quiz|test|project|practice|other), due_at, assigned_at, status (assigned|turned_in|missing|late|graded — only as rich as the source knows), score (nullable, HAC/API only), source, source_url, first_seen_at, last_seen_at, scraped_at`

Dedup/merge key: fuzzy match on (student, course, normalized title, due date ±1 day) — the same workbook page will appear in the agenda *and* HAC *and* guardian mail; the store keeps one driver with multiple source sightings, preferring the richest status (API/HAC for status, agenda for earliest warning).

**Resource**
`student, course/class, title, kind (notes|tutorial|answer_key|workbook|link|schedule), url, source, valid_from/to (tutorials are dated sessions), scraped_at`

**Driver run log** — one row per scrape: source, started_at, finished_at, ok, records_seen, error. This is the fix for this week's HAC-cache question: freshness becomes a fact, not an inference.

### MCP surface (new tools, existing grade tools kept)

- `get_drivers` — due today / this week / overdue, filterable by student + class, with status and source provenance
- `get_resources` — by class; "what exists for Thursday's volume quiz" (notes, key, tutorial session)
- `get_source_health` — per-driver last success, record counts, staleness flags
- Digest v2 consumes these: *due tomorrow + the notes link + which tutorial session covers it*, instead of just a missing list.

## 4. The Google Classroom problem (research summary)

Full research report was run 2026-10-22; the load-bearing findings:

1. **Official API — probably dead on arrival, but test it once.** A student token with the `*.me.readonly` scopes is technically perfect for this. Two independent district gates block it: (a) Google's default for Education domains **blocks under-18 users from unconfigured third-party apps** — consent hard-fails unless LISD IT allowlists our client ID; (b) a separate Classroom-specific admin toggle ("users can authorize apps to access Classroom data"). LISD's published posture (formal Software Approval Process, vendor agreements) says default-locked. Separately: an unpublished OAuth app in "Testing" mode gets **7-day refresh tokens** (not lights-out); the known workaround is publish-to-production without verification (unverified-app warning, 100-user cap — fine at n=1). **Plan: one 15-minute consent test in the setup session.** Success rewrites this plan for the better; failure costs nothing.
2. **Guardian email summaries — the sanctioned base layer.** Classroom's built-in guardian feature emails *Noah* daily/weekly summaries: missing work, upcoming work, class activity (never grades). They land in his own Gmail, which the system already reads — **no student credentials stored anywhere**. Caveats: LISD must have guardian access enabled, and **each teacher enables summaries per class individually** — real-world coverage is partial until the teachers flip their toggles. Setup is social, not technical: guardian-invite requests to Sam's teachers.
3. **Class calendars — cheap due-date feed if LISD allows sharing.** Classroom maintains a per-class calendar in Sam's Google Calendar. If LISD permits external sharing, either a secret ICS URL (poll from hal9k, zero auth state) or sharing the calendars to Noah's account gives a clean drivers-only feed. Due dates only — no status, no materials. One-minute check in the setup session.
4. **Headless browser — the enrichment layer, not the foundation.** Scraping the student **To-do page** (Assigned / Missing / Done across all classes) is the only route to full fidelity without admin cooperation. Non-negotiables from the research:
   - **Never automate the password form** (Google blocks automation-flagged sign-ins). A human logs in once, interactively, in the automation browser; runs reuse the persisted profile.
   - **Birth the profile on hal9k itself** (headed under Xvfb): first-login risk challenges happen in the environment that keeps the session; profiles don't transplant reliably between machines (Linux has no keyring headless → cookie encryption falls back to Chromium's basic obfuscation; create and run in the identical environment, `--password-store=basic`).
   - **Session life:** Workspace default web session is 14 days → plan a supervised re-login ritual roughly biweekly; the driver health-checks every run and raises "re-auth needed" instead of failing silently.
   - The profile directory **is a bearer credential** for Sam's school account: dedicated service user, 0700, treated deliberately.
   - Browser supply on ARM64: **Playwright's bundled Chromium** (officially supported on Ubuntu 24.04 arm64) is the default; Google began shipping official ARM64 Linux Chrome in 2026 (verify at build time — best fingerprint if the stable .deb installs); **avoid Ubuntu's chromium apt package** (snap wrapper, miserable in containers).
5. **Dominated routes — skip:** parsing Sam's own notification emails (inherits the same access gates, worse data), Google Takeout (not automatable), third-party connectors like Pipedream/IFTTT (same OAuth gates + ships a minor's data to a third-party cloud).

## 5. Freshness & failure behavior (a requirement, not a nicety)

Every record carries `scraped_at`; every driver logs runs. The digest and any future consumer must be able to say "agenda data is 3h old, guardian feed is current, HAC is 26h stale" instead of presenting a merged fiction. A driver that hasn't succeeded in N runs surfaces as a health alert (same pattern as the fare watch), not as silently missing data.

## 6. Build order

**Stage 0 — Supervised setup session (~30 min, Noah + Sam, one sitting)**
1. Classroom API consent test (personal GCP client, publish-to-production trick) → binary answer on both district gates.
2. Sam's Google Calendar settings → class-calendar sharing / secret ICS present? → enables or kills the gcal driver.
3. ~~Guardian-invite requests out to Sam's teachers~~ — **moot: guardian access is disabled district-wide (confirmed by the family 2026-10-22). No invites, no guardian driver.**
4. Only if 1–2 disappoint: birth the browser profile on hal9k (Xvfb login), confirm the To-do page renders in it.

**Stage 1 — Lights-out core (no dependencies on LISD or teachers)**
- Normalized store + run log; HAC wrapped as a driver (with scraped_at stamps retrofitted).
- Team-site driver (hub crawl, asset discovery) + agenda driver (alt-text parser, per-subject decks) + tutorial schedule → resources.
- `get_drivers` / `get_resources` / `get_source_health`; digest v2.

**Stage 2 — Sanctioned Classroom feeds (as they land)**
- ~~Guardian email parser~~ — dead: guardian access disabled district-wide.
- gcal driver if Stage 0 said yes. (With guardian gone, the calendar check matters more — it's the last sanctioned feed.)

**Stage 3 — Browser driver (only if the fidelity gap still matters)**
- Playwright + persistent profile; To-do page + per-class Classwork scraping; health-check + re-auth alerting; biweekly re-login ritual.

**Stage 4 — API driver (only if the Stage 0 test passed — if so, it jumps the queue to Stage 1.5)**

## 7. Risks & open questions

- **LISD gates** (API, calendar sharing, guardian enablement) — all unknowable until the Stage 0 session; the plan is deliberately useful even if all three are closed (Stage 1 + Stage 3 carry it).
- **Guardian coverage** depends on individual teachers; expect partial for weeks.
- **Deck format drift** — teachers edit decks by hand; the agenda parser needs tolerance (alt-text structure changes) and should snapshot raw HTML per run so a parse failure is debuggable after the fact.
- **Browser-driver maintenance drip** — SPA redesigns, session deaths, risk challenges. Accepted cost of Stage 3, bounded by health alerting.
- **Whose job is re-login?** The biweekly ritual needs an owner and a calendar nudge — propose folding it into the digest ("Classroom session expires in 2 days") rather than a separate nag.

## 8. Decisions for Noah

1. Schedule the Stage 0 session (needs Sam + his school credentials present; I can drive the checks live).
2. ~~OK to store Sam's Classroom browser profile on hal9k if Stage 3 becomes necessary (bearer-credential tradeoff, §4.4)?~~ **DECIDED 2026-10-22: Yes — Noah is fine holding Sam's credentials in an encrypted vault on hal9k for this ("nothing critical there"). Design: encrypted vault (service-user-only) + persistent profile as the primary session mechanism; vault credentials are the re-auth fallback when a session dies, with any Google challenge escalated to Noah rather than fought unattended (automated password-form logins are the highest-risk event in the system, per §4.4).
3. Guardian invites: I draft the teacher list + a one-line ask per teacher; you send (your standing rule: your correspondence is yours).
