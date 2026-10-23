"""Hub link extraction against the real captured hub page."""

from school.domain import ResourceKind
from school.driver_web.hub import (
    classify_link,
    extract_hub_links,
    fallback_title,
)
from driver_web.conftest import fixture_text


def _links() -> list:
    return extract_hub_links(fixture_text("team_site_home.html"))


def test_extracts_all_hub_links_from_real_fixture() -> None:
    links = _links()
    # 14 anchors: the self-link, 2 deck embeds, Doc, Sheet, 2 Drive
    # files, and 7 teacher-site links (younghistory appears twice with
    # different URLs and counts as two distinct links).
    assert len(links) == 14
    assert len({link.url for link in links}) == 14


def test_embed_labels_come_from_aria_with_chrome_stripped() -> None:
    by_kind = {link.kind: link for link in _links()}
    agenda = by_kind[ResourceKind.NOTES]
    assert agenda.label == "2026 - 2027 8th Grade Team Weekly Agenda"
    assert agenda.url.endswith("/present")
    tutorial = by_kind[ResourceKind.TUTORIAL]
    assert "Tutorial Schedule" in tutorial.label


def test_unlabelled_google_links_classify_by_url_shape() -> None:
    documents = [
        link for link in _links() if link.kind is ResourceKind.DOCUMENT
    ]
    assert len(documents) == 4  # Doc + Sheet + 2 Drive files
    assert all(link.label == "" for link in documents)
    urls = {link.url for link in documents}
    assert any("docs.google.com/document/" in url for url in urls)
    assert any("docs.google.com/spreadsheets/" in url for url in urls)
    assert sum("drive.google.com/file/" in url for url in urls) == 2


def test_teacher_links_are_plain_links_with_name_labels() -> None:
    plain = [link for link in _links() if link.kind is ResourceKind.LINK]
    # 7 teacher sites plus the hub's own self-link (the driver drops it).
    assert len(plain) == 8
    labels = {link.label for link in plain}
    assert "8th Grade Team Site" in labels
    assert "Sarah VanDyke" in labels
    assert "Kailey Goodie" in labels


def test_relative_hrefs_resolve_and_junk_is_skipped() -> None:
    html = """
    <a href="/leanderisd.org/some-page">Rel</a>
    <a href="mailto:teacher@example.com">Mail</a>
    <a href="#section">Jump</a>
    <a href="https://example.com/x">Ext</a>
    <a href="https://example.com/x">Ext again</a>
    """
    links = extract_hub_links(html)
    assert [link.url for link in links] == [
        "https://sites.google.com/leanderisd.org/some-page",
        "https://example.com/x",
    ]


def test_classify_link_rule_order() -> None:
    # Tutorial wins over the presentation->DOCUMENT rule.
    kind = classify_link(
        "Team Tutorial Schedule",
        "https://docs.google.com/presentation/d/abc/present",
    )
    assert kind is ResourceKind.TUTORIAL
    assert classify_link("", "https://docs.google.com/document/d/x") \
        is ResourceKind.DOCUMENT
    assert classify_link("anything", "https://example.com") \
        is ResourceKind.LINK


def test_fallback_titles() -> None:
    assert fallback_title("https://docs.google.com/document/d/x") \
        == "Google Doc"
    assert fallback_title("https://docs.google.com/spreadsheets/d/x") \
        == "Google Sheet"
    assert fallback_title("https://drive.google.com/file/d/x") \
        == "Drive file"
    assert fallback_title("https://example.com/whatever") == "Link"
