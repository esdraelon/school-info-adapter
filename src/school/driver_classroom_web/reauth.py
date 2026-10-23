"""ReauthHelper: ONE supervised-shape sign-in attempt from vault creds.

Pattern: Adapter over the SignInPage protocol with a hard stop rule
(PLAN Stage 3 / §4): the helper fills the standard Google form
(email -> Next -> password -> Next) and classifies the outcome. If
ANY challenge interstitial appears — 2-Step Verification, "Verify
it's you", CAPTCHA, recovery prompts — the outcome is CHALLENGE and
the helper STOPS: it never interacts with a challenge, never retries,
never tries to be clever. Challenges are escalated to a human via the
driver's failure message. This is deliberate: automated sign-ins are
the highest-risk event in the system, and a fought challenge can lock
the student's account.
"""

from enum import StrEnum

from school.driver_classroom_web.pages import SignInPage


class ReauthOutcome(StrEnum):
    """How a single re-auth attempt ended."""

    SIGNED_IN = "signed_in"
    CHALLENGE = "challenge"
    BAD_CREDENTIALS = "bad_credentials"
    FORM_NOT_FOUND = "form_not_found"


#: Challenge markers, matched case-insensitively against visible page
#: text. Any one of them ends the attempt immediately.
_CHALLENGE_MARKERS = (
    "2-step verification",
    "verify it's you",
    "verify it’s you",
    "captcha",
    "enter the code",
    "confirm your recovery",
    "check your phone",
    "this device isn't recognized",
    "this device isn’t recognized",
)


class ReauthHelper:
    """Drives one sign-in attempt; stateless and reusable."""

    def attempt(
        self, page: SignInPage, *, username: str, password: str
    ) -> ReauthOutcome:
        """Fill the standard form once and classify the result.

        Never raises for page-shape surprises — an unrecognized page
        is FORM_NOT_FOUND, and a challenge anywhere is CHALLENGE with
        no further interaction after the moment it is seen.
        """
        if self._challenge_visible(page):
            return ReauthOutcome.CHALLENGE
        if page.has_email_field():
            page.fill_email(username)
            page.advance()
            if self._challenge_visible(page):
                return ReauthOutcome.CHALLENGE
        if not page.has_password_field():
            if page.has_todo_structure():
                return ReauthOutcome.SIGNED_IN
            return ReauthOutcome.FORM_NOT_FOUND
        page.fill_password(password)
        page.advance()
        if self._challenge_visible(page):
            return ReauthOutcome.CHALLENGE
        if page.has_todo_structure():
            return ReauthOutcome.SIGNED_IN
        if "wrong password" in page.visible_text().casefold():
            return ReauthOutcome.BAD_CREDENTIALS
        return ReauthOutcome.FORM_NOT_FOUND

    def _challenge_visible(self, page: SignInPage) -> bool:
        text = page.visible_text().casefold()
        return any(marker in text for marker in _CHALLENGE_MARKERS)
