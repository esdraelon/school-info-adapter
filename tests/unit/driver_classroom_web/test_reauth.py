"""ReauthHelper against scripted FakeSignInPages.

The load-bearing assertion family here is the hard stop: when a
challenge appears, ``mutations`` (fills + advances) must END at the
interaction that revealed it — the helper never touches a challenge.
"""

from school.driver_classroom_web.reauth import ReauthHelper, ReauthOutcome
from tests.unit.driver_classroom_web.fakes import FakeSignInPage

HELPER = ReauthHelper()


def test_happy_path_signs_in_and_flips_the_probe() -> None:
    flipped: list[bool] = []
    page = FakeSignInPage(
        after_password="todo",
        on_signed_in=lambda: flipped.append(True),
    )
    outcome = HELPER.attempt(page, username="sam@example.test", password="pw")
    assert outcome is ReauthOutcome.SIGNED_IN
    assert page.filled_email == "sam@example.test"
    assert page.filled_password == "pw"
    assert page.mutations == [
        "fill_email",
        "advance",
        "fill_password",
        "advance",
    ]
    assert flipped == [True]


def test_challenge_after_password_stops_all_interaction() -> None:
    page = FakeSignInPage(after_password="challenge")
    outcome = HELPER.attempt(page, username="sam@example.test", password="pw")
    assert outcome is ReauthOutcome.CHALLENGE
    # The final mutation is the submit that revealed the challenge;
    # nothing — no fill, no advance — happens after it.
    assert page.mutations == [
        "fill_email",
        "advance",
        "fill_password",
        "advance",
    ]


def test_challenge_at_the_email_step_never_sees_the_password() -> None:
    page = FakeSignInPage(challenge_after_email=True)
    outcome = HELPER.attempt(page, username="sam@example.test", password="pw")
    assert outcome is ReauthOutcome.CHALLENGE
    assert page.mutations == ["fill_email", "advance"]
    assert page.filled_password is None


def test_challenge_already_on_the_page_means_zero_interaction() -> None:
    page = FakeSignInPage(after_password="challenge")
    page.advance()  # walk the fake to a state whose text is a challenge
    page.advance()
    page.mutations.clear()
    outcome = HELPER.attempt(page, username="sam@example.test", password="pw")
    assert outcome is ReauthOutcome.CHALLENGE
    assert page.mutations == []


def test_wrong_password_is_bad_credentials() -> None:
    page = FakeSignInPage(after_password="wrong")
    outcome = HELPER.attempt(page, username="sam@example.test", password="bad")
    assert outcome is ReauthOutcome.BAD_CREDENTIALS


def test_unrecognizable_end_page_is_form_not_found() -> None:
    page = FakeSignInPage(after_password="blank")
    outcome = HELPER.attempt(page, username="sam@example.test", password="pw")
    assert outcome is ReauthOutcome.FORM_NOT_FOUND


def test_no_fields_at_all_is_form_not_found() -> None:
    page = FakeSignInPage(email_field=False, password_field=False)
    outcome = HELPER.attempt(page, username="sam@example.test", password="pw")
    assert outcome is ReauthOutcome.FORM_NOT_FOUND
    assert page.mutations == []


def test_password_first_page_skips_the_email_step() -> None:
    # Some flows land directly on the password step (identifier known).
    page = FakeSignInPage(email_field=False, password_field=True)
    outcome = HELPER.attempt(page, username="sam@example.test", password="pw")
    assert outcome is ReauthOutcome.SIGNED_IN
    assert page.mutations == ["fill_password", "advance"]


def test_page_already_at_todo_is_signed_in_without_touching_it() -> None:
    page = FakeSignInPage(after_password="todo")
    page.advance()  # walk the fake to its signed-in end state
    page.advance()
    page.mutations.clear()
    outcome = HELPER.attempt(page, username="sam@example.test", password="pw")
    assert outcome is ReauthOutcome.SIGNED_IN
    assert page.mutations == []
