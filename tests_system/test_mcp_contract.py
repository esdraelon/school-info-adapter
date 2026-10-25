"""MCP contract pins (split milestone M0, design §4f–§4g).

A frozen fixture DB (fixed rows across HAC + TEAM_SITE + AGENDA;
Classroom inactive) is served by the CURRENT combined build over real
MCP-over-HTTP, and every tool call below is compared byte-for-byte
(canonical JSON) against a committed golden file in
``tests_system/goldens/test_mcp_contract/``.

Clocks are frozen in the fixtures (scrape stamps 2026-10-22T18:00Z,
health clock 2026-10-22T18:30Z), so goldens are byte-comparable —
the only clock-derived values (``scraped_at``, ``age_seconds``) are
fixed numbers, not wall-clock readings.

Purpose: this suite runs UNCHANGED against the new serving build in
split milestone M2. If a golden output moves, the split changed
behavior — that stops the split, it is not a golden to regenerate.

Regeneration (for the initial commit ONLY, or after a deliberate
contract decision with its own design amendment): run with
``CONTRACT_REGENERATE=1`` to rewrite the golden files from the
current build, then REVIEW the diff by hand before committing.
Never regenerate to make a red suite green.
"""

import asyncio
import json
import os
import socket
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from school.domain import (
    CourseRef,
    DriverKind,
    DriverRecord,
    ItemStatus,
    ResourceKind,
    ResourceRecord,
    RunOutcome,
    SourceKind,
    SourceRun,
    StudentRef,
)
from school.mcp_server import QueryServices, build_server
from school.store import (
    DriverRepository,
    ResourceRepository,
    RunLogRepository,
    file_connection_factory,
)

GOLDENS = Path(__file__).resolve().parent / "goldens" / "test_mcp_contract"
TOKEN = "contract-pin-token"

SAM = StudentRef(id="sam", display_name="Sam")
OTHER = StudentRef(id="other", display_name="Other")
SCRAPED = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)
HEALTH_NOW = SCRAPED + timedelta(minutes=30)

MATH = CourseRef(name="Math 8", period="3", teacher="Ms. Alvarez")
SCIENCE = CourseRef(name="Science", period="1", teacher="Mr. Chen")
ELA = CourseRef(name="ELA", period="2", teacher="Ms. Ortiz")
WIND = CourseRef(name="Wind Ensemble", period="6", teacher="Mr. Patel")
CHOIR = CourseRef(name="Choir", period="7", teacher="Ms. Nguyen")
BULK_COURSE = CourseRef(name="Bulk")


def _driver(
    title: str,
    course: CourseRef,
    kind: DriverKind,
    status: ItemStatus,
    source: SourceKind,
    due: datetime | None = None,
    score: str | None = None,
    student: StudentRef = SAM,
) -> DriverRecord:
    return DriverRecord(
        student=student,
        course=course,
        title=title,
        kind=kind,
        status=status,
        source_kind=source,
        scraped_at=SCRAPED,
        due_at=due,
        score_text=score,
        source_ref=f"fixture://{source.value}/{title}",
    )


def _resource(
    title: str,
    kind: ResourceKind,
    url: str,
    source: SourceKind,
    student: StudentRef | None = SAM,
    course: CourseRef | None = None,
) -> ResourceRecord:
    return ResourceRecord(
        title=title,
        kind=kind,
        url=url,
        source_kind=source,
        scraped_at=SCRAPED,
        student=student,
        course=course,
    )


def _run(
    kind: SourceKind,
    finished: datetime,
    outcome: RunOutcome,
    message: str | None = None,
) -> SourceRun:
    return SourceRun(
        source_kind=kind,
        started_at=finished - timedelta(minutes=5),
        finished_at=finished,
        outcome=outcome,
        found=10,
        new=2,
        updated=1,
        message=message,
    )


def _seed_curated(db_path: Path) -> None:
    """Fixed rows across HAC + TEAM_SITE + AGENDA; Classroom inactive."""
    factory = file_connection_factory(db_path)
    drivers = DriverRepository(factory)
    resources = ResourceRepository(factory)
    runs = RunLogRepository(factory)
    drivers.upsert_drivers(
        [
            # Dated, oldest-first ordering + title tiebreak on 2026-10-23.
            _driver(
                "Chemistry Unit Test",
                SCIENCE,
                DriverKind.TEST,
                ItemStatus.GRADED,
                SourceKind.HAC,
                due=datetime(2026, 9, 22, tzinfo=timezone.utc),
                score="92",
            ),
            _driver(
                "Timeline Project",
                ELA,
                DriverKind.PROJECT,
                ItemStatus.GRADED,
                SourceKind.HAC,
                due=datetime(2026, 10, 20, tzinfo=timezone.utc),
                score="87.77",
            ),
            _driver(
                "Notebook Check #1",
                ELA,
                DriverKind.ASSIGNMENT,
                ItemStatus.MISSING,
                SourceKind.HAC,
                due=datetime(2026, 10, 23, tzinfo=timezone.utc),
            ),
            _driver(
                "Unit 2 Assessment",
                SCIENCE,
                DriverKind.TEST,
                ItemStatus.MISSING,
                SourceKind.HAC,
                due=datetime(2026, 10, 23, tzinfo=timezone.utc),
            ),
            _driver(
                "Choir Participation 10/19-23",
                CHOIR,
                DriverKind.PRACTICE,
                ItemStatus.DONE,
                SourceKind.TEAM_SITE,
                due=datetime(2026, 10, 24, tzinfo=timezone.utc),
            ),
            # Due exactly at the 2026-10-26 midnight bound: pins that a
            # date-only window bound means midnight, inclusively.
            _driver(
                "Quiz: Pythagorean Theorem",
                MATH,
                DriverKind.QUIZ,
                ItemStatus.DONE,
                SourceKind.AGENDA,
                due=datetime(2026, 10, 26, tzinfo=timezone.utc),
            ),
            # Same calendar day, 08:00: inside a >=2026-10-26 window,
            # outside a <=2026-10-26 window.
            _driver(
                "Sectionals Week 2-5",
                WIND,
                DriverKind.PRACTICE,
                ItemStatus.ASSIGNED,
                SourceKind.AGENDA,
                due=datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc),
            ),
            _driver(
                "Algebra Readiness Quiz",
                MATH,
                DriverKind.QUIZ,
                ItemStatus.ASSIGNED,
                SourceKind.AGENDA,
                due=datetime(2027, 4, 23, tzinfo=timezone.utc),
            ),
            # Undated: last in every unwindowed call, excluded by windows.
            _driver(
                "Extra Credit: Math Puzzle",
                MATH,
                DriverKind.PRACTICE,
                ItemStatus.ASSIGNED,
                SourceKind.AGENDA,
            ),
            _driver(
                "Science Fair Idea",
                SCIENCE,
                DriverKind.PROJECT,
                ItemStatus.ASSIGNED,
                SourceKind.TEAM_SITE,
            ),
            # Another student: pins student_id scoping.
            _driver(
                "Other Student Assignment",
                MATH,
                DriverKind.ASSIGNMENT,
                ItemStatus.ASSIGNED,
                SourceKind.HAC,
                due=datetime(2026, 10, 25, tzinfo=timezone.utc),
                student=OTHER,
            ),
        ]
    )
    resources.upsert_resources(
        [
            _resource(
                "Volume notes",
                ResourceKind.NOTES,
                "https://example.test/math/volume-notes",
                SourceKind.AGENDA,
                course=MATH,
            ),
            _resource(
                "Area/Perimeter",
                ResourceKind.NOTES,
                "https://example.test/math/area-perimeter",
                SourceKind.AGENDA,
                course=MATH,
            ),
            # Shared (no student, no course): visible in every view.
            _resource(
                "Tutorial Schedule",
                ResourceKind.TUTORIAL,
                "https://example.test/team/tutorials",
                SourceKind.TEAM_SITE,
                student=None,
                course=None,
            ),
            _resource(
                "Science Lab Safety",
                ResourceKind.DOCUMENT,
                "https://example.test/science/lab-safety",
                SourceKind.HAC,
                course=SCIENCE,
            ),
            # Shared student, but coursed: a course filter for another
            # course must NOT match it (it is not courseless).
            _resource(
                "Choir Warmups",
                ResourceKind.LINK,
                "https://example.test/choir/warmups",
                SourceKind.TEAM_SITE,
                student=None,
                course=CHOIR,
            ),
            _resource(
                "General Study Tips",
                ResourceKind.LINK,
                "https://example.test/study-tips",
                SourceKind.TEAM_SITE,
                student=None,
                course=None,
            ),
            # Another student's row: excluded from Sam's view.
            _resource(
                "Other Student Notes",
                ResourceKind.NOTES,
                "https://example.test/other/notes",
                SourceKind.HAC,
                student=OTHER,
                course=MATH,
            ),
            _resource(
                "ELA Essay Guide",
                ResourceKind.DOCUMENT,
                "https://example.test/ela/essay",
                SourceKind.AGENDA,
                course=ELA,
            ),
            # Owned but courseless: matches any course filter for Sam.
            _resource(
                "Math Practice Set",
                ResourceKind.DOCUMENT,
                "https://example.test/math/practice",
                SourceKind.AGENDA,
                course=None,
            ),
        ]
    )
    # HAC: an older FAILURE superseded by a SUCCESS — latest wins.
    runs.append_run(
        _run(SourceKind.HAC, SCRAPED - timedelta(hours=6), RunOutcome.FAILURE,
              message="cache unreadable")
    )
    runs.append_run(_run(SourceKind.HAC, SCRAPED, RunOutcome.SUCCESS))
    runs.append_run(
        _run(SourceKind.TEAM_SITE, SCRAPED - timedelta(hours=1),
              RunOutcome.SUCCESS)
    )
    runs.append_run(
        _run(SourceKind.AGENDA, SCRAPED - timedelta(hours=2),
              RunOutcome.FAILURE, message="deck fetch failed: 404")
    )
    # CLASSROOM_WEB: no run at all — never-run nulls.


def _seed_bulk(db_path: Path) -> None:
    """205 drivers + 205 resources: past the default limit of 200."""
    factory = file_connection_factory(db_path)
    drivers = DriverRepository(factory)
    resources = ResourceRepository(factory)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    drivers.upsert_drivers(
        [
            _driver(
                f"Bulk {n:03d}",
                BULK_COURSE,
                DriverKind.ASSIGNMENT,
                ItemStatus.ASSIGNED,
                SourceKind.HAC,
                due=base + timedelta(days=n - 1),
            )
            for n in range(1, 206)
        ]
    )
    resources.upsert_resources(
        [
            _resource(
                f"Bulk Resource {n:03d}",
                ResourceKind.DOCUMENT,
                f"https://example.test/bulk/{n:03d}",
                SourceKind.AGENDA,
                course=None,
            )
            for n in range(1, 206)
        ]
    )


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _boot(tmp_path_factory: pytest.TempPathFactory, name: str) -> Iterator[str]:
    tmp = tmp_path_factory.mktemp(name)
    db_path = tmp / "contract.db"
    if name == "contract":
        _seed_curated(db_path)
    else:
        _seed_bulk(db_path)
    factory = file_connection_factory(db_path)
    services = QueryServices(
        drivers=DriverRepository(factory),
        resources=ResourceRepository(factory),
        runs=RunLogRepository(factory),
        clock=lambda: HEALTH_NOW,
    )
    app = build_server(services, expected_token=TOKEN)
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(500):
        if server.started:
            break
        time.sleep(0.01)
    assert server.started, "MCP server did not start"
    try:
        yield f"http://127.0.0.1:{port}/"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    yield from _boot(tmp_path_factory, "contract")


@pytest.fixture(scope="module")
def bulk_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    yield from _boot(tmp_path_factory, "contract_bulk")


def _call_raw(url: str, name: str, args: dict[str, Any]) -> Any:
    """One MCP tool call over real HTTP; returns the raw SDK result."""

    async def go() -> Any:
        http_client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {TOKEN}"},
            trust_env=False,
            follow_redirects=True,
        )
        async with streamable_http_client(
            url, http_client=http_client
        ) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(name, args)

    return asyncio.run(go())


def call_tool(url: str, name: str, args: dict[str, Any]) -> Any:
    """One MCP tool call over real HTTP via the SDK client."""
    result = _call_raw(url, name, args)
    assert not result.isError, f"{name} failed: {result.content}"
    if result.structuredContent is not None:
        return result.structuredContent["result"]
    blocks = [
        json.loads(block.text) for block in result.content if block.type == "text"
    ]
    if len(blocks) == 1 and isinstance(blocks[0], list):
        return blocks[0]
    return blocks


def _check_golden(name: str, actual: Any) -> None:
    """Canonical-JSON byte comparison against the committed golden."""
    canonical = json.dumps(actual, sort_keys=True, indent=2) + "\n"
    golden_path = GOLDENS / f"{name}.json"
    if os.environ.get("CONTRACT_REGENERATE"):
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(canonical)
        return
    assert golden_path.read_text() == canonical, (
        f"golden {name} moved — the serving contract changed; "
        "do not regenerate to make this green"
    )


# --- Drivers: golden calls on the curated stack ---


def test_drivers_default_golden(stack: str) -> None:
    _check_golden(
        "drivers_default", call_tool(stack, "get_drivers", {"student_id": "sam"})
    )


def test_drivers_status_missing_golden(stack: str) -> None:
    _check_golden(
        "drivers_status_missing",
        call_tool(
            stack, "get_drivers", {"student_id": "sam", "status": "MISSING"}
        ),
    )


def test_drivers_status_graded_golden(stack: str) -> None:
    _check_golden(
        "drivers_status_graded",
        call_tool(stack, "get_drivers", {"student_id": "sam", "status": "GRADED"}),
    )


def test_drivers_window_oct_golden(stack: str) -> None:
    _check_golden(
        "drivers_window_oct",
        call_tool(
            stack,
            "get_drivers",
            {
                "student_id": "sam",
                "due_on_or_after": "2026-10-20",
                "due_on_or_before": "2026-10-26",
            },
        ),
    )


def test_drivers_window_after_golden(stack: str) -> None:
    _check_golden(
        "drivers_window_after",
        call_tool(
            stack,
            "get_drivers",
            {"student_id": "sam", "due_on_or_after": "2026-10-26"},
        ),
    )


def test_drivers_window_before_golden(stack: str) -> None:
    _check_golden(
        "drivers_window_before",
        call_tool(
            stack,
            "get_drivers",
            {"student_id": "sam", "due_on_or_before": "2026-10-26"},
        ),
    )


def test_drivers_window_empty_golden(stack: str) -> None:
    _check_golden(
        "drivers_window_empty",
        call_tool(
            stack,
            "get_drivers",
            {"student_id": "sam", "due_on_or_after": "2999-01-01"},
        ),
    )


def test_drivers_course_math_golden(stack: str) -> None:
    _check_golden(
        "drivers_course_math",
        call_tool(
            stack,
            "get_drivers",
            {"student_id": "sam", "course_name": "Math 8"},
        ),
    )


def test_drivers_limit_small_golden(stack: str) -> None:
    _check_golden(
        "drivers_limit_small",
        call_tool(stack, "get_drivers", {"student_id": "sam", "limit": 2}),
    )


def test_drivers_other_student_golden(stack: str) -> None:
    _check_golden(
        "drivers_other_student",
        call_tool(stack, "get_drivers", {"student_id": "other"}),
    )


def test_drivers_missing_student_id_is_a_tool_error(stack: str) -> None:
    """get_drivers requires student_id; the wrong name is not a scope."""
    result = _call_raw(stack, "get_drivers", {"student": "sam"})
    assert result.isError
    text = json.dumps([block.text for block in result.content])
    assert "student_id" in text


# --- Resources: golden calls on the curated stack ---


def test_resources_sam_golden(stack: str) -> None:
    _check_golden(
        "resources_sam",
        call_tool(stack, "get_resources", {"student_id": "sam"}),
    )


def test_resources_unscoped_golden(stack: str) -> None:
    _check_golden("resources_unscoped", call_tool(stack, "get_resources", {}))


def test_resources_kind_notes_sam_golden(stack: str) -> None:
    _check_golden(
        "resources_kind_notes_sam",
        call_tool(
            stack, "get_resources", {"student_id": "sam", "kind": "NOTES"}
        ),
    )


def test_resources_course_math_sam_golden(stack: str) -> None:
    _check_golden(
        "resources_course_math_sam",
        call_tool(
            stack,
            "get_resources",
            {"student_id": "sam", "course_name": "Math 8"},
        ),
    )


def test_resources_course_science_sam_golden(stack: str) -> None:
    _check_golden(
        "resources_course_science_sam",
        call_tool(
            stack,
            "get_resources",
            {"student_id": "sam", "course_name": "Science"},
        ),
    )


def test_resources_limit_small_golden(stack: str) -> None:
    _check_golden(
        "resources_limit_small",
        call_tool(stack, "get_resources", {"student_id": "sam", "limit": 2}),
    )


def test_resources_unknown_arg_returns_unscoped(stack: str) -> None:
    """Unknown arguments are IGNORED, not rejected (contract wart, §4f).

    The wrong parameter name ``student`` does not scope the call:
    the result must equal the unscoped golden exactly.
    """
    actual = call_tool(stack, "get_resources", {"student": "sam"})
    _check_golden("resources_unscoped", actual)


# --- Health + serializer field sets ---


def test_source_health_golden(stack: str) -> None:
    _check_golden(
        "source_health", call_tool(stack, "get_source_health", {})
    )


def test_serializer_field_sets(stack: str) -> None:
    """Exact wire field sets, independent of golden ordering."""
    driver = call_tool(
        stack, "get_drivers", {"student_id": "sam", "limit": 1}
    )[0]
    assert set(driver) == {
        "student",
        "course",
        "title",
        "kind",
        "status",
        "due_at",
        "score_text",
        "source_kind",
        "source_ref",
        "scraped_at",
    }
    assert set(driver["student"]) == {"id", "display_name"}
    assert set(driver["course"]) == {"name", "period", "teacher"}
    resource = call_tool(
        stack, "get_resources", {"student_id": "sam", "limit": 1}
    )[0]
    assert set(resource) == {
        "student",
        "course",
        "title",
        "kind",
        "url",
        "source_kind",
        "scraped_at",
    }
    health = call_tool(stack, "get_source_health", {})
    assert len(health) == 4
    for entry in health:
        assert set(entry) == {
            "source_kind",
            "last_outcome",
            "last_finished_at",
            "age_seconds",
            "message",
        }
    assert [entry["source_kind"] for entry in health] == [
        "HAC",
        "TEAM_SITE",
        "AGENDA",
        "CLASSROOM_WEB",
    ]


def test_drivers_default_undated_last(stack: str) -> None:
    """Undated items sort last (structural pin beside the golden)."""
    drivers = call_tool(stack, "get_drivers", {"student_id": "sam"})
    undated = [d for d in drivers if d["due_at"] is None]
    assert [d["title"] for d in undated] == [
        "Extra Credit: Math Puzzle",
        "Science Fair Idea",
    ]
    assert drivers[-2:] == undated


# --- Limit-200 truncation shape on the bulk stack ---


def test_bulk_drivers_default_truncates_at_200(bulk_stack: str) -> None:
    """Default call: exactly 200, oldest-first, newest truncated first."""
    default = call_tool(
        bulk_stack, "get_drivers", {"student_id": "sam"}
    )
    assert len(default) == 200
    assert default[0]["title"] == "Bulk 001"
    assert default[-1]["title"] == "Bulk 200"
    assert all(d["title"] <= "Bulk 200" for d in default)


def test_bulk_drivers_large_limit_returns_all(bulk_stack: str) -> None:
    """Explicit-large-limit leg: 205, and the default is its prefix."""
    all_rows = call_tool(
        bulk_stack, "get_drivers", {"student_id": "sam", "limit": 100000}
    )
    default = call_tool(bulk_stack, "get_drivers", {"student_id": "sam"})
    assert len(all_rows) == 205
    assert all_rows[:200] == default
    assert all_rows[-1]["title"] == "Bulk 205"


def test_bulk_resources_default_truncates_at_200(bulk_stack: str) -> None:
    """Resources default: exactly 200, title order, tail truncated."""
    default = call_tool(
        bulk_stack, "get_resources", {"student_id": "sam"}
    )
    assert len(default) == 200
    assert default[0]["title"] == "Bulk Resource 001"
    assert default[-1]["title"] == "Bulk Resource 200"


def test_bulk_resources_large_limit_returns_all(bulk_stack: str) -> None:
    """Explicit-large-limit leg: 205, and the default is its prefix."""
    all_rows = call_tool(
        bulk_stack, "get_resources", {"student_id": "sam", "limit": 100000}
    )
    default = call_tool(bulk_stack, "get_resources", {"student_id": "sam"})
    assert len(all_rows) == 205
    assert all_rows[:200] == default
    assert all_rows[-1]["title"] == "Bulk Resource 205"
