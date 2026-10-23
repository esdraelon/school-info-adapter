"""Team-site hub link extraction. Pattern: pure extractor + rule table.

``extract_hub_links`` turns the hub page's HTML into typed links. Two
quirks of the real page drive the design (found in the M3 capture):

- The Google embeds (agenda deck, tutorial deck) are anchors whose
  *text* is empty; their human label lives in the anchor's own
  ``aria-label`` ("Open Presentation, 2026 - 2027 8th Grade Team
  Weekly Agenda in new window"). Labels therefore fall back
  text -> aria-label/title, with the "Open ..., ... in new window"
  chrome stripped.
- The Doc / Sheet / Drive links carry no label at all; classification
  and fallback titles come from the URL shape alone.

Classification is an ordered rule table over (label, URL) — the first
matching rule wins (C2-style data-driven selection, not an if/else
chain). Self-links to the hub page itself are dropped by the caller.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin

from school.domain import ResourceKind

HUB_BASE = "https://sites.google.com"


@dataclass(frozen=True, kw_only=True)
class HubLink:
    """One outbound link from the hub page, classified."""

    url: str
    label: str
    kind: ResourceKind


class _AnchorCollector(HTMLParser):
    """Collects (href, text, aria-label) for every <a> in the page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[tuple[str, str, str]] = []
        self._href: str | None = None
        self._aria: str = ""
        self._text: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag != "a" or self._href is not None:
            return
        values = dict(attrs)
        href = values.get("href")
        if href:
            self._href = href
            self._aria = values.get("aria-label") or values.get("title") or ""
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            text = " ".join("".join(self._text).split())
            self.anchors.append((self._href, text, self._aria))
            self._href = None
            self._aria = ""
            self._text = []


_OPEN_CHROME = re.compile(
    r"^Open\s+(?:Presentation|Document|Spreadsheet|File),\s*", re.IGNORECASE
)
_NEW_WINDOW = re.compile(r"\s+in new window$", re.IGNORECASE)


def _clean_label(text: str, aria: str) -> str:
    """Best human label for an anchor: its text, else its aria-label
    with the "Open X, ... in new window" chrome removed."""
    if text:
        return text
    label = _OPEN_CHROME.sub("", aria.strip())
    return _NEW_WINDOW.sub("", label).strip()


_Rule = Callable[[str, str], bool]

_KIND_RULES: tuple[tuple[_Rule, ResourceKind], ...] = (
    (lambda label, url: "tutorial" in label or "tutorial" in url,
     ResourceKind.TUTORIAL),
    (lambda label, url: "agenda" in label or "agenda" in url,
     ResourceKind.NOTES),
    (lambda label, url: "docs.google.com/document/" in url,
     ResourceKind.DOCUMENT),
    (lambda label, url: "docs.google.com/spreadsheets/" in url,
     ResourceKind.DOCUMENT),
    (lambda label, url: "docs.google.com/presentation/" in url,
     ResourceKind.DOCUMENT),
    (lambda label, url: "drive.google.com/file/" in url,
     ResourceKind.DOCUMENT),
)

_FALLBACK_TITLES: tuple[tuple[str, str], ...] = (
    ("docs.google.com/document/", "Google Doc"),
    ("docs.google.com/spreadsheets/", "Google Sheet"),
    ("docs.google.com/presentation/", "Presentation"),
    ("drive.google.com/file/", "Drive file"),
)


def classify_link(label: str, url: str) -> ResourceKind:
    """The ResourceKind for a hub link, via the ordered rule table."""
    lowered_label, lowered_url = label.lower(), url.lower()
    for matches, kind in _KIND_RULES:
        if matches(lowered_label, lowered_url):
            return kind
    return ResourceKind.LINK


def fallback_title(url: str) -> str:
    """A display title for an unlabelled link, from its URL shape."""
    for fragment, title in _FALLBACK_TITLES:
        if fragment in url:
            return title
    return "Link"


def extract_hub_links(html: str, *, base: str = HUB_BASE) -> list[HubLink]:
    """Extract the hub page's outbound links, deduped by URL.

    Relative hrefs resolve against ``base`` (Google Sites serves page
    links root-relative). Non-HTTP hrefs (mailto:, #fragments, ...)
    are skipped.
    """
    collector = _AnchorCollector()
    collector.feed(html)
    links: list[HubLink] = []
    seen: set[str] = set()
    for href, text, aria in collector.anchors:
        if not (href.startswith(("http://", "https://", "/"))):
            continue
        url = urljoin(base, href)
        if url in seen:
            continue
        seen.add(url)
        label = _clean_label(text, aria)
        links.append(
            HubLink(url=url, label=label, kind=classify_link(label, url))
        )
    return links
