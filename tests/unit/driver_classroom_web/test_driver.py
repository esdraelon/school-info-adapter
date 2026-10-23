"""ClassroomWebDriver against FakeSessions (browser layer scripted;
vault + credentials are the REAL objects over tmp files).
"""

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import pytest

from school.domain import ItemStatus, RunOutcome, SourceKind, StudentRef
from school.driver_classroom_web import (
    RELOGIN_MESSAGE,
    ClassroomStudentConfig,
    ClassroomWebDriver,
    SessionState,
    TodoTab,
)
from school.driver_classroom_web.pages import Row
from school.spi import ScrapeContext
from school.vault import Vault, VaultCredentials
from tests.unit.driver_classroom_web.fakes import (
    FakeGroup,
    FakeSession,
    FakeSignInPage,
    FakeTodoPage,
)

NOW = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)
SAM = StudentRef(id="sam", display_name="Sam")
ACCOUNT = "sam@example.test"
COURSES = ["Math 8", "Gholizadeh 8th Science", "Wind Ensemble"]
CONTEXT = ScrapeContext(student=SAM, now=NOW)


def _pages() -> dict[TodoTab, FakeTodoPage]:
    return {
        TodoTab.ASSIGNED: FakeTodoPage(
            courses=COURSES,
            groups={
                "This week": FakeGroup(
                    rows=[
                        Row(
                            name="Fractions Quiz Math 8 Tomorrow, 9:00 AM",
                            href="/c/1",
                        )
                    ]
                )
            },
        ),
        TodoTab.MISSING: FakeTodoPage(
            courses=COURSES,
            groups={
                "Earlier": FakeGroup(
                    rows=[
                        Row(
                            name="Newton Practice Gholizadeh 8th "
                            "Science Friday, Oct 9",
                            href="/c/2",
                        )
                    ]
                )
            },
        ),
        TodoTab.DONE: FakeTodoPage(
            courses=COURSES,
            groups={
                "This week": FakeGroup(
                    rows=[
                        Row(
                            name="Scale Run Wind Ensemble 90 points out "
                            "of possible 100 Turned in",
                            href="/c/3",
                        )
                    ]
                )
            },
        ),
    }


def _driver(
    session_factory: Callable[[], FakeSession],
    credentials: VaultCredentials | None = None,
) -> ClassroomWebDriver:
    return ClassroomWebDriver(
        session_factory=session_factory,
        credentials=credentials,
        student=ClassroomStudentConfig(
            account_id=ACCOUNT, expected_email=ACCOUNT
        ),
        clock=lambda: NOW,
    )


@pytest.fixture
def credentials(tmp_path: Path) -> VaultCredentials:
    vault = Vault(
        vault_path=tmp_path / "vault.json", key_path=tmp_path / "vault.key"
    )
    vault.unlock()
    vault.put(f"{ACCOUNT}.username", ACCOUNT)
    vault.put(f"{ACCOUNT}.password", "pw-123")
    return VaultCredentials(vault)


def test_kind_is_classroom_web() -> None:
    driver = _driver(lambda: FakeSession(pages=_pages()))
    assert driver.kind is SourceKind.CLASSROOM_WEB


def test_authenticated_scrape_maps_all_three_tabs() -> None:
    session = FakeSession(pages=_pages())
    result = _driver(lambda: session).scrape(CONTEXT)
    assert result.run.outcome is RunOutcome.SUCCESS
    assert result.run.found == 3
    assert result.run.source_kind is SourceKind.CLASSROOM_WEB
    by_status = {record.status: record for record in result.drivers}
    assert by_status[ItemStatus.ASSIGNED].title == "Fractions Quiz"
    assert by_status[ItemStatus.MISSING].title == "Newton Practice"
    graded = by_status[ItemStatus.GRADED]
    assert graded.score_text == "90 points out of possible 100"
    assert session.opened_tabs == [
        TodoTab.ASSIGNED,
        TodoTab.MISSING,
        TodoTab.DONE,
    ]
    assert session.closed is True


def test_expired_without_credentials_fails_with_relogin_wording() -> None:
    session = FakeSession(
        probe_states=[SessionState.EXPIRED], pages=_pages()
    )
    result = _driver(lambda: session, credentials=None).scrape(CONTEXT)
    assert result.run.outcome is RunOutcome.FAILURE
    assert result.run.message is not None
    assert RELOGIN_MESSAGE in result.run.message
    assert ACCOUNT in result.run.message
    assert session.opened_tabs == []  # never reached extraction
    assert session.closed is True


def test_expired_with_empty_vault_fails_with_relogin_wording(
    tmp_path: Path,
) -> None:
    vault = Vault(
        vault_path=tmp_path / "vault.json", key_path=tmp_path / "vault.key"
    )
    vault.unlock()
    session = FakeSession(probe_states=[SessionState.EXPIRED])
    driver = _driver(lambda: session, credentials=VaultCredentials(vault))
    result = driver.scrape(CONTEXT)
    assert result.run.outcome is RunOutcome.FAILURE
    assert result.run.message is not None
    assert RELOGIN_MESSAGE in result.run.message


def test_expired_then_reauth_signs_in_and_scrapes(
    credentials: VaultCredentials,
) -> None:
    session = FakeSession(probe_states=[SessionState.EXPIRED], pages=_pages())
    session.script_sign_in(after_password="todo")
    result = _driver(lambda: session, credentials=credentials).scrape(CONTEXT)
    assert result.run.outcome is RunOutcome.SUCCESS
    assert result.run.found == 3
    assert session.probe_calls == 2  # probe, re-auth, re-probe
    sign_in = session.sign_in_page()
    assert sign_in.filled_email == ACCOUNT
    assert sign_in.filled_password == "pw-123"


def test_expired_then_challenge_fails_and_stops(
    credentials: VaultCredentials,
) -> None:
    sign_in = FakeSignInPage(after_password="challenge")
    session = FakeSession(
        probe_states=[SessionState.EXPIRED],
        pages=_pages(),
        sign_in_page=sign_in,
    )
    result = _driver(lambda: session, credentials=credentials).scrape(CONTEXT)
    assert result.run.outcome is RunOutcome.FAILURE
    assert result.run.message is not None
    assert RELOGIN_MESSAGE in result.run.message
    assert "challenge" in result.run.message
    # The helper stopped at the challenge: no tab was ever opened.
    assert session.opened_tabs == []
    assert sign_in.mutations == [
        "fill_email",
        "advance",
        "fill_password",
        "advance",
    ]


def test_expired_then_bad_credentials_fail_with_wording(
    credentials: VaultCredentials,
) -> None:
    session = FakeSession(
        probe_states=[SessionState.EXPIRED],
        pages=_pages(),
        sign_in_page=FakeSignInPage(after_password="wrong"),
    )
    result = _driver(lambda: session, credentials=credentials).scrape(CONTEXT)
    assert result.run.outcome is RunOutcome.FAILURE
    assert result.run.message is not None
    assert RELOGIN_MESSAGE in result.run.message
    assert "rejected" in result.run.message


def test_still_expired_after_signed_in_report_is_a_failure(
    credentials: VaultCredentials,
) -> None:
    # The helper claims SIGNED_IN but the session never flips:
    # the re-probe is the authority, and it says EXPIRED.
    session = FakeSession(
        probe_states=[SessionState.EXPIRED],
        pages=_pages(),
        sign_in_page=FakeSignInPage(after_password="todo"),
    )
    result = _driver(lambda: session, credentials=credentials).scrape(CONTEXT)
    assert result.run.outcome is RunOutcome.FAILURE
    assert result.run.message is not None
    assert RELOGIN_MESSAGE in result.run.message


def test_unknown_probe_fails_with_the_probe_detail() -> None:
    session = FakeSession(probe_states=[SessionState.UNKNOWN])
    result = _driver(lambda: session).scrape(CONTEXT)
    assert result.run.outcome is RunOutcome.FAILURE
    assert result.run.message is not None
    assert "inconclusive" in result.run.message
    assert "fake probe detail" in result.run.message


def test_one_failing_tab_is_partial_and_names_the_tab() -> None:
    session = FakeSession(
        pages=_pages(),
        open_errors={TodoTab.MISSING: RuntimeError("tab exploded")},
    )
    result = _driver(lambda: session).scrape(CONTEXT)
    assert result.run.outcome is RunOutcome.PARTIAL
    assert result.run.found == 2
    assert len(result.drivers) == 2
    assert result.run.message is not None
    assert "missing" in result.run.message
    assert "tab exploded" in result.run.message


def test_all_tabs_failing_is_a_failure() -> None:
    session = FakeSession(
        pages=_pages(),
        open_errors={
            tab: RuntimeError("nope") for tab in TodoTab
        },
    )
    result = _driver(lambda: session).scrape(CONTEXT)
    assert result.run.outcome is RunOutcome.FAILURE
    assert result.drivers == []
    assert result.run.message is not None
    assert "all classroom tabs failed" in result.run.message


def test_session_factory_failure_is_a_run_failure() -> None:
    def factory() -> FakeSession:
        raise RuntimeError("chromium missing")

    result = _driver(factory).scrape(CONTEXT)
    assert result.run.outcome is RunOutcome.FAILURE
    assert result.run.message is not None
    assert "failed to start" in result.run.message


def test_probe_failure_is_a_run_failure() -> None:
    session = FakeSession(probe_error=RuntimeError("browser died"))
    result = _driver(lambda: session).scrape(CONTEXT)
    assert result.run.outcome is RunOutcome.FAILURE
    assert result.run.message is not None
    assert "probe failed" in result.run.message
    assert session.closed is True


def test_close_failure_does_not_sink_the_run() -> None:
    session = FakeSession(
        pages=_pages(), close_error=RuntimeError("context already gone")
    )
    result = _driver(lambda: session).scrape(CONTEXT)
    assert result.run.outcome is RunOutcome.SUCCESS
    assert session.closed is True


def test_probe_failure_after_successful_reauth(
    credentials: VaultCredentials,
) -> None:
    session = FakeSession(
        probe_states=[SessionState.EXPIRED],
        pages=_pages(),
        fail_second_probe=True,
    )
    session.script_sign_in(after_password="todo")
    result = _driver(lambda: session, credentials=credentials).scrape(CONTEXT)
    assert result.run.outcome is RunOutcome.FAILURE
    assert result.run.message is not None
    assert "after re-auth" in result.run.message


def test_reauth_attempt_error_is_a_run_failure(
    credentials: VaultCredentials,
) -> None:
    session = FakeSession(
        probe_states=[SessionState.EXPIRED],
        sign_in_error=RuntimeError("page detached"),
    )
    result = _driver(lambda: session, credentials=credentials).scrape(CONTEXT)
    assert result.run.outcome is RunOutcome.FAILURE
    assert result.run.message is not None
    assert RELOGIN_MESSAGE in result.run.message
    assert "errored" in result.run.message


def test_tampered_vault_is_a_distinct_run_failure(tmp_path: Path) -> None:
    import base64
    import json

    vault_path = tmp_path / "vault.json"
    vault = Vault(vault_path=vault_path, key_path=tmp_path / "vault.key")
    vault.unlock()
    vault.put(f"{ACCOUNT}.username", ACCOUNT)
    vault.put(f"{ACCOUNT}.password", "pw-123")
    payload = json.loads(vault_path.read_text(encoding="utf-8"))
    raw = bytearray(
        base64.b64decode(payload["entries"][f"{ACCOUNT}.password"]["ciphertext"])
    )
    raw[0] ^= 0xFF
    payload["entries"][f"{ACCOUNT}.password"]["ciphertext"] = (
        base64.b64encode(bytes(raw)).decode()
    )
    vault_path.write_text(json.dumps(payload), encoding="utf-8")
    fresh = Vault(vault_path=vault_path, key_path=tmp_path / "vault.key")
    fresh.unlock()

    session = FakeSession(probe_states=[SessionState.EXPIRED])
    driver = _driver(lambda: session, credentials=VaultCredentials(fresh))
    result = driver.scrape(CONTEXT)
    assert result.run.outcome is RunOutcome.FAILURE
    assert result.run.message is not None
    assert "credential vault error" in result.run.message
