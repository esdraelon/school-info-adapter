# M3 fixture capture — provenance

Captured 2026-10-22 from the live public pages (no auth). All fetches are
plain HTTP with a browser User-Agent.

| File | Source | Form |
|---|---|---|
| `team_site_home.html` | https://sites.google.com/leanderisd.org/8th-grade-team-site/home | Full page, verbatim (119,273 bytes) |
| `math_agenda_pub.html` | Math 8 deck `/pub` (see deck_registry.json) | **Trimmed skeleton** of the 40 MB page: all 12 slides kept |
| `algebra_agenda_pub.html` | Algebra I deck `/pub` | Trimmed skeleton: slides 0–1 of 17 |
| `team_agenda_embed.html` | Team agenda deck `/embed` | Trimmed skeleton: slides 0–2 of 42 |
| `tutorial_embed.html` | Tutorial Schedule deck `/embed` | Trimmed skeleton: slides 0–2 of 42 |
| `tutorial_export.txt` | Tutorial Schedule deck `/export/txt` | Full text export, verbatim (cross-check for the SVG parse) |
| `deck_registry.json` | Discovery across hub + team agenda links | Hand-written from the captures |

## Why the deck fixtures are "skeletons"

A published Slides page inlines each slide as an `SK_svgData = '...'`
JavaScript string holding a full vector rendering (~1–5 MB per slide,
almost entirely glyph `<path>` geometry). The parse-relevant content is
the accessibility layer: `<g role="img" aria-label="...">` text blocks
and `<a>` link elements, each carrying a transparent hit-rect `<path>`
whose coordinates place the block on the slide grid.

The trimmer kept, per slide: the `<svg>` root, every a11y `<g>` element
(with its first geometry path), and every `<a>` element (with its first
path) — re-escaped in the page's own JS-string format. It validated that
the (kind, label/href, rect) element stream of every skeleton is
identical to the full page's, slide for slide. Parsers therefore see
exactly the structure they see in production, minus rendering bulk.
