# System tests

End-to-end tests for the School Information Adapter live in this directory
(`tests_system/`), separate from `tests/unit/`. The first system test
arrived with the MCP server milestone (M4): `test_end_to_end.py` boots
drivers -> merge -> store -> MCP-over-HTTP against stub-served fixtures.
This file records the philosophy the suite is built under, per convention
C7 of `docs/DESIGN.md`.

## Philosophy: mock externals only (C7)

System tests boot the **real** composition root and exercise the entire
stack: real SQLite store, real MCP transport over HTTP, real parsers, real
merge logic, real Playwright + real Chromium for the Classroom driver.

The only things replaced are the **external boundaries** — the systems on
the other side of the network that we do not own:

- HAC / the existing grades cache,
- Google Sites / published Slides endpoints (team site, agenda decks),
- classroom.google.com.

Those are served by a **local stub HTTP server** returning captured real
fixtures (agenda deck pub-HTML, team-site HTML, Classroom pages captured by
the real driver), so a test failure means *our* stack broke, never that a
fixture was idealized — fixtures are captured reality, not hand-written
HTML (see `docs/FIXTURES.md` for the Classroom To-do capture).

Rules:

- No mocking of internal collaborators in system tests. If a test needs to
  fake one of our own components, it belongs in `tests/unit/` instead, where
  mocking immediate collaborators is allowed.
- System tests gate merges (a milestone branch does not merge red), but
  they do **not** count toward the ≥90% unit-coverage gate (C6) — coverage
  is measured over `tests/unit/` only.
- Every system test must be able to state its data's freshness: records
  carry `scraped_at` and every scrape writes a run-log row (C10), so tests
  assert on provenance, not just content.
