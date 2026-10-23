"""Slide extraction against the real captured deck pages."""

from school.driver_web.slides import (
    decode_js_string,
    extract_slide_alts,
    unwrap_url,
)
from driver_web.conftest import fixture_text


def test_math_deck_extracts_twelve_slides_in_order() -> None:
    slides = extract_slide_alts(fixture_text("math_agenda_pub.html"))
    assert len(slides) == 12
    first_texts = [slide.texts[0].text for slide in slides]
    assert all("Math Weekly Agenda" in text for text in first_texts)


def test_first_slide_texts_and_links() -> None:
    slide = extract_slide_alts(fixture_text("math_agenda_pub.html"))[0]
    assert len(slide.texts) == 17
    assert len(slide.links) == 14
    headers = [t.text.split("\n")[0] for t in slide.texts]
    assert "Monday 10/19 " in headers or "Monday 10/19" in headers
    # Every element carries geometry on the real page.
    assert all(t.rect is not None for t in slide.texts)
    assert all(link.rect is not None for link in slide.links)


def test_links_are_unwrapped_from_google_redirects() -> None:
    slide = extract_slide_alts(fixture_text("math_agenda_pub.html"))[0]
    urls = {link.url for link in slide.links}
    assert not any("google.com/url?q=" in url for url in urls)
    assert (
        "https://www.ixl.com/math/grade-8/"
        "converse-of-the-pythagorean-theorem-is-it-a-right-triangle"
    ) in urls
    assert (
        "https://drive.google.com/file/d/"
        "1BkPPWP2CKYo-K5kSZxsAQPPGHOH30Via/view?usp=sharing"
    ) in urls
    labels = {link.label for link in slide.links}
    assert "Area/Perimeter" in labels
    assert "Student workbook" in labels


def test_embed_pages_extract_the_same_way() -> None:
    slides = extract_slide_alts(fixture_text("team_agenda_embed.html"))
    assert len(slides) == 3
    assert "Team Weekly Agenda" in slides[0].texts[0].text
    assert len(slides[0].links) == 14


def test_page_without_slides_extracts_nothing() -> None:
    assert extract_slide_alts("<html><body>plain</body></html>") == []


def test_decode_js_string_escapes() -> None:
    assert decode_js_string(r"\x3cg\x3e") == "<g>"
    assert decode_js_string(r"https:\/\/x\/y") == "https://x/y"
    assert decode_js_string(r"it\'s") == "it's"
    assert decode_js_string(r"a\\b") == "a\\b"
    assert decode_js_string("caf\u2019e") == "caf\u2019e"


def test_unwrap_url_passthrough() -> None:
    plain = "https://drive.google.com/file/d/abc/view"
    assert unwrap_url(plain) == plain
    wrapped = (
        "https://www.google.com/url?q=https%3A%2F%2Fexample.com%2Fa"
        "&sa=D&source=editors"
    )
    assert unwrap_url(wrapped) == "https://example.com/a"
