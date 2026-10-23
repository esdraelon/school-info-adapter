"""Published-Slides page extraction. Pattern: Adapter.

How a published (``/pub``) or embedded (``/embed``) Slides page actually
segments — determined from the M3 fixture capture, since it is *not*
the loose pile of ``<img alt>`` tags the format is often assumed to be:

- Each slide is inlined in a ``<script>`` block as
  ``SK_svgData = '<escaped SVG>';`` followed by
  ``SK_viewerApp.setPageData('<page id>', SK_svgData, [])``. Document
  order of the assignments is slide order (newest week first).
- The SVG is a full vector rendering. Its *accessibility layer* is the
  parseable content: every text block is a
  ``<g role="img" aria-label="...">`` whose aria-label carries the
  block's full text (newlines as ``&#xa;``), and every hyperlink is an
  ``<a xlink:href="...">`` element. Both carry a transparent hit-rect
  ``<path>`` (``m x y l0 h l w 0 l0 -h z``) whose coordinates place the
  element on the 960x540 slide — that geometry is what assigns grid
  cells their day columns.
- Link hrefs are Google redirect wrappers
  (``https://www.google.com/url?q=<target>&sa=D&...``); extraction
  unwraps them to the real target.

``extract_slide_alts`` is the single entry point: page HTML in, one
``SlideAlt`` per slide out, texts and links each in document order.
"""

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

_ASSIGN_RE = re.compile(r"SK_svgData\s*=\s*'((?:[^'\\]|\\.)*)'", re.DOTALL)
_HEX = set("0123456789abcdefABCDEF")
_SIMPLE_ESCAPES = {
    "n": "\n", "r": "\r", "t": "\t", "b": "\b",
    "f": "\f", "v": "\v", "0": "\0",
}


def decode_js_string(raw: str) -> str:
    """Decode a single-quoted JS string literal's escape sequences
    (``\\xNN``, ``\\uNNNN``, ``\\/``, ``\\\\``, quote escapes)."""
    out: list[str] = []
    i = 0
    while i < len(raw):
        char = raw[i]
        if char != "\\" or i + 1 >= len(raw):
            out.append(char)
            i += 1
            continue
        nxt = raw[i + 1]
        if nxt == "x" and i + 3 < len(raw) and set(raw[i + 2 : i + 4]) <= _HEX:
            out.append(chr(int(raw[i + 2 : i + 4], 16)))
            i += 4
        elif (
            nxt == "u"
            and i + 5 < len(raw)
            and set(raw[i + 2 : i + 6]) <= _HEX
        ):
            out.append(chr(int(raw[i + 2 : i + 6], 16)))
            i += 6
        elif nxt in _SIMPLE_ESCAPES:
            out.append(_SIMPLE_ESCAPES[nxt])
            i += 2
        else:  # \/, \\, \', \" and any other escaped char stand for it
            out.append(nxt)
            i += 2
    return "".join(out)


@dataclass(frozen=True, kw_only=True)
class Rect:
    """An element's hit-rect on the slide (960x540 coordinate space)."""

    x: float
    y: float
    width: float
    height: float

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2


@dataclass(frozen=True, kw_only=True)
class SlideText:
    """One text block: its full text and where it sits on the slide."""

    text: str
    rect: Rect | None


@dataclass(frozen=True, kw_only=True)
class SlideLink:
    """One hyperlink: unwrapped target URL, label if any, position."""

    url: str
    label: str | None
    rect: Rect | None


@dataclass(frozen=True, kw_only=True)
class SlideAlt:
    """One slide's accessibility layer: text blocks + links, in order."""

    texts: tuple[SlideText, ...]
    links: tuple[SlideLink, ...]


_RECT_RE = re.compile(
    r"^m\s*(-?[\d.]+)[ ,](-?[\d.]+)l0\s*(-?[\d.]+)l\s*(-?[\d.]+)"
    r"\s+0l0\s*(-?[\d.]+)z"
)


_POINT_RE = re.compile(r"^m\s*(-?[\d.]+)[ ,](-?[\d.]+)")


def _parse_rect(d: str | None) -> Rect | None:
    """Parse a hit-rect path (``m x y l0 h l w 0 l0 -h z``), if it is one.

    Link paths may carry several subpaths (one per wrapped line of the
    linked text); the first subpath's rect is the position used. When
    the first subpath isn't a clean rect (wrapped-line underlines have
    extra segments), fall back to a point rect at the path's start —
    column assignment needs only the position.
    """
    if not d:
        return None
    match = _RECT_RE.match(d.strip())
    if match is not None:
        x, y, height, width, _neg = (float(g) for g in match.groups())
        return Rect(x=x, y=y, width=width, height=height)
    point = _POINT_RE.match(d.strip())
    if point is not None:
        return Rect(x=float(point.group(1)), y=float(point.group(2)),
                    width=0.0, height=0.0)
    return None


def unwrap_url(url: str) -> str:
    """Unwrap Google's ``/url?q=`` redirect hrefs to their target."""
    parsed = urlparse(url)
    if parsed.netloc.endswith("google.com") and parsed.path == "/url":
        targets = parse_qs(parsed.query).get("q")
        if targets:
            return targets[0]
    return url


class _SlideScanner(HTMLParser):
    """Collects a11y text blocks and links from one slide's SVG."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.texts: list[SlideText] = []
        self.links: list[SlideLink] = []
        self._g_depth = 0
        self._g_label: str | None = None
        self._g_rect: Rect | None = None
        self._a_open = False
        self._a_href: str = ""
        self._a_label: str | None = None
        self._a_rect: Rect | None = None

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        values = dict(attrs)
        if tag == "g":
            if self._g_depth > 0:
                self._g_depth += 1
            elif values.get("role") == "img" and values.get("aria-label"):
                self._g_depth = 1
                self._g_label = values["aria-label"]
                self._g_rect = None
        elif tag == "a" and not self._a_open:
            self._a_open = True
            self._a_href = values.get("xlink:href") or values.get("href") or ""
            self._a_label = values.get("aria-label")
            self._a_rect = None
        elif tag == "path":
            rect = _parse_rect(values.get("d"))
            if rect is not None:
                if self._g_depth > 0 and self._g_rect is None:
                    self._g_rect = rect
                if self._a_open and self._a_rect is None:
                    self._a_rect = rect

    def handle_endtag(self, tag: str) -> None:
        if tag == "g" and self._g_depth > 0:
            self._g_depth -= 1
            if self._g_depth == 0 and self._g_label is not None:
                self.texts.append(
                    SlideText(text=self._g_label, rect=self._g_rect)
                )
                self._g_label = None
        elif tag == "a" and self._a_open:
            self.links.append(
                SlideLink(
                    url=unwrap_url(self._a_href),
                    label=self._a_label,
                    rect=self._a_rect,
                )
            )
            self._a_open = False


def extract_slide_alts(page_html: str) -> list[SlideAlt]:
    """One SlideAlt per slide in a pub/embed page, in document order."""
    slides: list[SlideAlt] = []
    for match in _ASSIGN_RE.finditer(page_html):
        scanner = _SlideScanner()
        scanner.feed(decode_js_string(match.group(1)))
        slides.append(
            SlideAlt(texts=tuple(scanner.texts), links=tuple(scanner.links))
        )
    return slides
