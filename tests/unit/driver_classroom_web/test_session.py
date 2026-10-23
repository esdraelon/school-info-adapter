"""Session: the pure probe classification + ClassroomSession flow
against a fake PageAdapter (no browser anywhere in unit tests, C7).
"""

from pathlib import Path

import pytest

from school.driver_classroom_web.session import (
    ClassroomSession,
    ProfileConfig,
    SessionState,
    classify_page,
)
from school.driver_classroom_web.tabs import TodoTab
from tests.unit.driver_classroom_web.fakes import FakePageAdapter

TODO_URL = "https://classroom.google.com/a/not-turned-in/all"
SIGNIN_URL = "https://accounts.google.com/v3/signin/identifier"


@pytest.mark.parametrize(
    "url,signin_form,landmark,expected",
    [
        (TODO_URL, False, True, SessionState.AUTHENTICATED),
        (SIGNIN_URL, True, False, SessionState.EXPIRED),
        (TODO_URL, True, False, SessionState.EXPIRED),
        (SIGNIN_URL, False, False, SessionState.EXPIRED),
        # A sign-in surface wins even over leftover Classroom chrome.
        (SIGNIN_URL, True, True, SessionState.EXPIRED),
        (TODO_URL, False, False, SessionState.UNKNOWN),
        ("https://classroom.google.com/", False, False, SessionState.UNKNOWN),
    ],
)
def test_classify_page(
    url: str,
    signin_form: bool,
    landmark: bool,
    expected: SessionState,
) -> None:
    assert (
        classify_page(
            url=url,
            signin_form_present=signin_form,
            todo_landmark_present=landmark,
        )
        is expected
    )


def test_profile_config_defaults_carry_the_hal9k_launch_args() -> None:
    config = ProfileConfig(profile_dir=Path("/tmp/profile"))
    assert config.headless is True
    assert config.base_url == "https://classroom.google.com"
    assert "--password-store=basic" in config.launch_args
    assert "--no-first-run" in config.launch_args


def _session(adapter: FakePageAdapter) -> ClassroomSession:
    config = ProfileConfig(profile_dir=Path("/tmp/profile"))
    return ClassroomSession(config, adapter_factory=lambda: adapter)


def test_probe_authenticated_records_detail() -> None:
    adapter = FakePageAdapter(signin_form=False, landmark=True)
    session = _session(adapter)
    assert session.probe() is SessionState.AUTHENTICATED
    assert adapter.visited == [TODO_URL]
    assert "todo_landmark=True" in session.last_probe_detail


def test_probe_expired_on_signin_form() -> None:
    adapter = FakePageAdapter(signin_form=True, landmark=False)
    assert _session(adapter).probe() is SessionState.EXPIRED


def test_probe_unknown_when_neither_surface_shows() -> None:
    adapter = FakePageAdapter(signin_form=False, landmark=False)
    assert _session(adapter).probe() is SessionState.UNKNOWN


def test_open_todo_navigates_to_the_tab_url() -> None:
    adapter = FakePageAdapter()
    session = _session(adapter)
    page = session.open_todo(TodoTab.DONE)
    assert adapter.visited == ["https://classroom.google.com/a/turned-in/all"]
    assert page is adapter.todo_page()


def test_sign_in_page_comes_from_the_adapter() -> None:
    adapter = FakePageAdapter()
    session = _session(adapter)
    assert session.sign_in_page() is adapter.sign_in_page()


def test_base_url_override_drives_navigation() -> None:
    config = ProfileConfig(
        profile_dir=Path("/tmp/profile"), base_url="http://127.0.0.1:9999"
    )
    adapter = FakePageAdapter()
    session = ClassroomSession(config, adapter_factory=lambda: adapter)
    session.open_todo(TodoTab.MISSING)
    assert adapter.visited == ["http://127.0.0.1:9999/a/missing/all"]


def test_close_is_idempotent_and_adapter_is_lazy() -> None:
    created: list[int] = []

    def factory() -> FakePageAdapter:
        created.append(1)
        return FakePageAdapter()

    session = ClassroomSession(
        ProfileConfig(profile_dir=Path("/tmp/profile")),
        adapter_factory=factory,
    )
    session.close()  # never started: no factory call, no error
    assert created == []
    session.probe()
    session.close()
    session.close()
    assert created == [1]
