"""Fetch a web page and keep its main article text (Reading → Text, ``mine reading``).

An own extractor on ``lxml`` (already a runtime dependency) instead of
trafilatura, which would add about 41 MB to every install (F-14, master plan).
The method is the classic text-density one: drop what is never article text
(scripts, navigation, asides, footers, forms, share and comment blocks), score
each paragraph-like block by its length and punctuation, credit the score to
the block's parent and half to its grandparent, discount a container by how
much of its text is link text, and keep the best container's paragraphs. The
paragraphs come from the EPUB loader's own walker (``epub_source._walk_body``),
so a web page and a book chapter break into paragraphs the same way.

No per-site rules (D-008 calls them fragile) and no headless browser: a page
whose article is drawn by JavaScript has no text in its HTML and is reported
as ``no_text``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from lxml import etree, html  # type: ignore[import-untyped]

from anki_miner.services.reading._util import _line_join
from anki_miner.services.reading.epub_source import _walk_body

if TYPE_CHECKING:
    from anki_miner.languages.profile import SentenceRules

logger = logging.getLogger(__name__)

#: Fewer article characters than this is a page with no article (a JavaScript
#: shell, a login wall, an index of links).
MIN_ARTICLE_CHARS = 80
#: A block shorter than this scores nothing (a caption, a byline, a button).
_MIN_BLOCK_CHARS = 25

ArticleFailure = Literal["invalid_url", "network", "http_status", "not_html", "too_large", "no_text"]

#: Elements whose whole subtree is never article text.
_DROP_TAGS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "template",
        "nav",
        "aside",
        "footer",
        "form",
        "button",
        "iframe",
        "svg",
        "select",
        "input",
        "textarea",
        "object",
        "embed",
        "canvas",
    }
)
_DROP_ROLES = frozenset({"navigation", "banner", "contentinfo", "complementary", "search", "dialog"})
#: class/id words of blocks that are never article text, matched as whole
#: ``-``/``_``/space-separated words so "header-image" or "shadow" never match.
_JUNK_WORD = re.compile(
    r"(?:^|[-_\s])(?:comments?|share|sharing|social|related|recommend(?:ed|ations)?|promo|advert(?:isement)?|ads?"
    r"|cookies?|consent|newsletter|subscribe|sidebar|breadcrumbs?|menu|nav|navbar|footer|masthead|popup|modal)(?:$|[-_\s])",
    re.IGNORECASE,
)
#: Blocks whose own text scores for their container. A ``div`` with no block
#: inside it scores too: many pages write paragraphs as ``div`` + ``br``.
_SCORED_TAGS = frozenset({"p", "pre", "blockquote", "td", "dd"})
_BLOCK_TAGS = frozenset(
    {"p", "div", "section", "article", "ul", "ol", "table", "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6"}
)
_PUNCTUATION = re.compile(r"[,.!?;:、。，．！？；：]")
_SPACES = re.compile(r"[ \t 　]+")
_XML_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>")


@dataclass(frozen=True)
class WebArticle:
    """One fetched article: its address, title, site and plain text.

    ``text`` holds one paragraph per block, separated by a blank line, the
    shape the Text loader splits (``text_source.load``).
    """

    url: str
    title: str
    site: str
    text: str

    @property
    def source_label(self) -> str:
        """The card Source: ``title — site``, or whichever of the two exists."""
        return " — ".join(part for part in (self.title, self.site) if part)


class ArticleFetchError(Exception):
    """A page could not be fetched, or held no article text.

    ``reason`` says which (the GUI words it in the interface language; the
    message is the English text the command line reports). ``transient`` is
    true for a failure a later retry can fix: a network error, HTTP 429 or 5xx.
    """

    def __init__(self, url: str, reason: ArticleFailure, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.url = url
        self.reason: ArticleFailure = reason
        self.transient = transient


def is_web_url(text: str) -> bool:
    """True for an absolute ``http``/``https`` address with a host."""
    try:
        parts = urlsplit(text.strip())
        host = parts.hostname
    except ValueError:
        return False
    return parts.scheme.lower() in ("http", "https") and bool(host)


def parse_url_lines(text: str) -> tuple[list[str], list[str]]:
    """Split a one-address-per-line box into ``(addresses, rejected lines)``.

    Blank lines are ignored, and a repeated address is kept once, in
    first-seen order.
    """
    urls: list[str] = []
    rejected: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if not is_web_url(line):
            rejected.append(line)
        elif line not in urls:
            urls.append(line)
    return urls, rejected


def _site(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _clean(text: str) -> str:
    return _SPACES.sub(" ", text).strip()


def _title(doc) -> str:
    for xpath in ('//meta[@property="og:title"]/@content', '//meta[@name="twitter:title"]/@content'):
        found = doc.xpath(xpath)
        if found and _clean(str(found[0])):
            return _clean(str(found[0]))
    titles = doc.xpath("//title")
    if titles and _clean(titles[0].text_content()):
        return _clean(titles[0].text_content())
    headings = doc.xpath("//h1")
    return _clean(headings[0].text_content()) if headings else ""


def _is_junk(el) -> bool:
    if not isinstance(el.tag, str):
        return False
    if el.tag.lower() in _DROP_TAGS:
        return True
    if (el.get("role") or "").lower() in _DROP_ROLES:
        return True
    if el.get("aria-hidden") == "true" or el.get("hidden") is not None:
        return True
    marker = f"{el.get('class') or ''} {el.get('id') or ''}"
    return _JUNK_WORD.search(marker) is not None


def _drop_junk(body) -> None:
    for el in list(body.iter()):
        if el is body or el.getparent() is None:
            continue
        if _is_junk(el):
            el.drop_tree()


def _link_density(el) -> float:
    total = len(_clean(el.text_content()))
    if not total:
        return 1.0
    linked = sum(len(_clean(a.text_content())) for a in el.iter("a"))
    return min(1.0, linked / total)


def _is_leaf_div(el) -> bool:
    if el.tag != "div":
        return False
    return not any(isinstance(child.tag, str) and child.tag.lower() in _BLOCK_TAGS for child in el.iterdescendants())


def _best_container(body):
    scores: dict[object, float] = {}
    for el in body.iter():
        if not isinstance(el.tag, str):
            continue
        if el.tag.lower() not in _SCORED_TAGS and not _is_leaf_div(el):
            continue
        text = _clean(el.text_content())
        if len(text) < _MIN_BLOCK_CHARS:
            continue
        parent = el.getparent()
        if parent is None:
            continue
        score = 1.0 + len(_PUNCTUATION.findall(text)) + min(len(text) // 100, 3)
        scores[parent] = scores.get(parent, 0.0) + score
        grandparent = parent.getparent()
        if grandparent is not None:
            scores[grandparent] = scores.get(grandparent, 0.0) + score / 2
    if not scores:
        return None
    return max(scores, key=lambda el: scores[el] * (1.0 - _link_density(el)))


def extract_article(page: str, url: str, *, rules: SentenceRules | None = None) -> WebArticle:
    """Keep the main article text of one decoded HTML page.

    ``rules`` is the mining language's sentence policy; it decides only how a
    source line wrap inside a paragraph is joined (a space, or nothing for
    CJK). The title is ``og:title``, then ``twitter:title``, then ``<title>``,
    then the first ``<h1>``; the site is the host without ``www.``. Raises
    :class:`ArticleFetchError` (``no_text``) when the page holds fewer than
    :data:`MIN_ARTICLE_CHARS` characters of article text.
    """
    try:
        doc = html.document_fromstring(_XML_DECLARATION.sub("", page, count=1))
    except (etree.ParserError, etree.XMLSyntaxError, ValueError) as exc:
        raise ArticleFetchError(url, "no_text", f"No article text found at {url}.") from exc
    title = _title(doc)
    body = doc.find("body")
    if body is None:
        body = doc
    _drop_junk(body)
    container = _best_container(body)
    paragraphs: list[str] = []
    if container is not None:
        walked, _images = _walk_body(container, line_join=_line_join(rules))
        paragraphs = [cleaned for cleaned in (_clean(p) for p in walked) if cleaned]
    text = "\n\n".join(paragraphs)
    if len(text) < MIN_ARTICLE_CHARS:
        raise ArticleFetchError(url, "no_text", f"No article text found at {url}.")
    return WebArticle(url=url, title=title, site=_site(url), text=text)
