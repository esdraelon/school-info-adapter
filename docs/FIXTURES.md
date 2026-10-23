# Google Classroom To-do page — accessibility-tree map

**Captured:** 2026-10-22, live session (Samuel Smith, samuel.smith26@k12.leanderisd.org), read-only.
**Purpose:** M6 `driver-classroom-web` extraction spec. Raw HTML could not be saved from the
capture browser (no view-source/DevTools route) — this map is the fixture spec instead.
Raw-HTML fixtures will be captured by the real Playwright driver itself on hal9k at M6
(it can dump outerHTML trivially once the hal9k-born profile exists).

**Design consequence:** extract via the accessibility tree (Playwright `getByRole` /
aria snapshots), NOT CSS classes. Roles + accessible names are stable; Classroom's
CSS class names are obfuscated and rotate.

## URLs
- Assigned: https://classroom.google.com/a/not-turned-in/all
- Missing:  https://classroom.google.com/a/missing/all
- Done:     https://classroom.google.com/a/turned-in/all

## Page structure
- Tab strip: navigation landmark `Page navigation`, link nodes `Assigned` / `Missing` / `Done`.
  Active tab's accessible name has ` (selected)` appended.
- Header: h1 `Google Classroom Classroom To-do`; combobox `Class filter` (value `All classes`);
  region `Informational banner`.

## Groups (level-2 headings; each followed by an expand/collapse button named `<heading> <count>`)
- Assigned: `No due date`, `This week`, `Next week`, `Later`
- Missing: `This week`, `Last week`, `Earlier`
- Done: `No due date`, `Done early`, `This week`, `Last week`, `Earlier`
- Counts observed 2026-10-22 — Assigned 8/4/1/1; Missing 3/2/11 (=16); Done 2/4/6/7/114.
- Groups with >5 visible items show a `View all` button after the visible items —
  extractors MUST expand groups / handle `View all` to see everything.

## Item rows
Every row is ONE link node; its accessible name = title + class name + right-column text.
Layout: class-colored circular icon (clipboard glyph; `?` for question posts), bold title
over secondary class name, right-aligned status column.

- **Assigned** right column = due date (green): `Today, 11:59 PM`, `Tomorrow, 9:00 AM`,
  `Wednesday, Oct 28`, `Wednesday, Dec 9`; no-due-date items: `Posted Monday, Aug 24`.
- **Missing** right column = due date (red — color is the ONLY lateness marker, no label text):
  `Today, 9:00 AM`, `Yesterday, 11:59 PM`, `Friday, Oct 9`, `Wednesday, Oct 7`.
- **Done** right column = grade + status, NO dates: `87.77 points out of possible 100`,
  statuses `Not turned in` / `Turned in` / `Done late`.

## Due-text grammar (for the date parser fixture table)
- Near-term: `<Today|Tomorrow|Yesterday|Weekday>, <h>:<mm> <AM|PM>`
- Farther: `<Weekday>, <Mon> <D>` (no year, no time)
- No-due (Assigned only): `Posted <Weekday>, <Mon> <D>`
