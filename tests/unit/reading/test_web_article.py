"""Tests for the web article extractor and fetcher (F-14).

Every page is a local string: nothing here touches the network (the socket
tripwire in ``tests/_network_tripwire.py`` would fail it).
"""

from __future__ import annotations

import pytest
import requests

from anki_miner.languages.registry import get_profile
from anki_miner.services.reading import web_article
from anki_miner.services.reading.web_article import (
    ArticleFetchError,
    WebArticle,
    extract_article,
    fetch_article,
    fetch_articles,
    is_web_url,
    parse_url_lines,
)

_NEWS_JA = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html><head><title>猫の話 | 例のニュース</title>
<meta property="og:title" content="猫の話">
<script>var tracking = "吾輩はスクリプトである。";</script></head>
<body>
<header class="site-header"><nav><a href="/">ホーム</a> <a href="/news">ニュース一覧</a></nav></header>
<div id="main"><article><h1>猫の話</h1>
<p>吾輩は猫である。名前はまだ無い。どこで生れたかとんと見当がつかぬ。</p>
<p>何でも薄暗いじめじめした所でニャーニャー泣いていた事だけは記憶している。</p>
<p>吾輩はここで始めて人間というものを見た。しかもあとで聞くとそれは書生という人間中で一番獰悪な種族であったそうだ。</p>
<div class="share-buttons"><a href="#">シェアする</a> <a href="#">ツイートする</a></div>
</article>
<aside><p>関連記事：犬の話、鳥の話、魚の話、そのほかいろいろな話がここに並んでいます。</p></aside>
<div class="comments"><p>コメント：とても面白かったです。続きが読みたいです。次回も楽しみにしています。</p></div>
</div>
<footer><p>Copyright 2026 Example News. All rights reserved. Contact us for more information.</p></footer>
</body></html>
"""

_ARTICLE_JA = (
    "猫の話\n\n"
    "吾輩は猫である。名前はまだ無い。どこで生れたかとんと見当がつかぬ。\n\n"
    "何でも薄暗いじめじめした所でニャーニャー泣いていた事だけは記憶している。\n\n"
    "吾輩はここで始めて人間というものを見た。しかもあとで聞くとそれは書生という人間中で一番獰悪な種族であったそうだ。"
)

_BLOG_ES = """<html><head><title>Un paseo por Madrid - Mi blog</title></head><body>
<div class="menu"><a href="/">Inicio</a> <a href="/sobre-mi">Sobre mí</a></div>
<div class="post-body">El primer párrafo del artículo está partido
en dos líneas del código fuente, y sigue lo bastante para contar.<br>
La segunda línea después de un salto, también larga para contar como bloque.<br>
<p>Un párrafo de verdad con muchas palabras, comas, y un punto al final de la frase.</p>
</div></body></html>"""

_JS_ONLY = (
    '<html><head><title>App</title></head><body><div id="root"></div>'
    '<script src="app.js"></script>'
    "<noscript>Please enable JavaScript to view this page. This site needs JavaScript to run at all.</noscript>"
    "</body></html>"
)


def test_keeps_the_article_and_drops_page_chrome() -> None:
    article = extract_article(_NEWS_JA, "https://www.example.com/neko")

    assert article.text == _ARTICLE_JA
    for chrome in ("ホーム", "シェア", "関連記事", "コメント", "Copyright", "スクリプト"):
        assert chrome not in article.text


def test_title_prefers_og_title_and_site_drops_www() -> None:
    article = extract_article(_NEWS_JA, "https://www.example.com/neko")

    assert (article.title, article.site) == ("猫の話", "example.com")
    assert article.source_label == "猫の話 — example.com"


def test_title_falls_back_to_the_title_tag() -> None:
    article = extract_article(_BLOG_ES, "http://blog.example.org/madrid", rules=get_profile("es").sentence_rules)

    assert article.title == "Un paseo por Madrid - Mi blog"
    assert article.site == "blog.example.org"


def test_a_space_delimited_language_joins_a_wrapped_line_with_a_space() -> None:
    """Review Focus 3: a non-Japanese profile's rules decide the line join."""
    article = extract_article(_BLOG_ES, "http://blog.example.org/madrid", rules=get_profile("es").sentence_rules)

    assert article.text.split("\n\n") == [
        "El primer párrafo del artículo está partido en dos líneas del código fuente, y sigue lo bastante para contar.",
        "La segunda línea después de un salto, también larga para contar como bloque.",
        "Un párrafo de verdad con muchas palabras, comas, y un punto al final de la frase.",
    ]
    assert "Inicio" not in article.text


def test_japanese_joins_a_wrapped_line_without_a_space() -> None:
    page = _NEWS_JA.replace("名前はまだ無い。", "名前は\nまだ無い。")

    article = extract_article(page, "https://www.example.com/neko", rules=get_profile("ja").sentence_rules)

    assert "名前はまだ無い。" in article.text


def test_a_javascript_only_page_reports_no_article_text() -> None:
    with pytest.raises(ArticleFetchError) as excinfo:
        extract_article(_JS_ONLY, "https://spa.example.com/")

    assert excinfo.value.reason == "no_text"
    assert excinfo.value.transient is False
    assert str(excinfo.value) == "No article text found at https://spa.example.com/."


def test_a_prefixed_tag_inside_the_article_keeps_its_text() -> None:
    """HTML from a word processor carries ``<o:p>`` (and widgets ``<fb:like>``); lxml keeps the prefix."""
    page = (
        "<html><body><article>"
        "<p>El primer párrafo del artículo, con comas, y un punto al final.<o:p></o:p></p>"
        "<p>El segundo párrafo, también largo, sigue contando la historia.<fb:like>Me gusta</fb:like></p>"
        "</article></body></html>"
    )

    article = extract_article(page, "https://example.com/a", rules=get_profile("es").sentence_rules)

    assert "El segundo párrafo, también largo, sigue contando la historia." in article.text


def test_source_label_with_only_a_site() -> None:
    assert WebArticle(url="u", title="", site="example.com", text="x").source_label == "example.com"


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("https://example.com/a", True),
        ("http://example.com", True),
        ("  https://example.com/a  ", True),
        ("example.com/a", False),
        ("ftp://example.com/a", False),
        ("https://", False),
        ("https://[::1", False),
    ],
)
def test_is_web_url(text: str, ok: bool) -> None:
    assert is_web_url(text) is ok


def test_parse_url_lines_keeps_order_drops_blanks_and_repeats() -> None:
    box = "https://a.example/x\n\n  example.com\nhttps://a.example/x\nftp://f.example/y\nhttp://b.example\n"

    assert parse_url_lines(box) == (
        ["https://a.example/x", "http://b.example"],
        ["example.com", "ftp://f.example/y"],
    )


# --------------------------------------------------------------------------- #
# Fetching, over a fake session (never the network)
# --------------------------------------------------------------------------- #


class _FakeResponse:
    def __init__(self, body: bytes, *, status: int = 200, content_type: str = "text/html", url: str = "") -> None:
        self.status_code = status
        self.headers = requests.structures.CaseInsensitiveDict({"Content-Type": content_type} if content_type else {})
        self.url = url
        self._body = body
        self.closed = False

    def iter_content(self, chunk_size: int):
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start : start + chunk_size]

    def close(self) -> None:
        self.closed = True


class _FakeSession:
    def __init__(self, *responses: _FakeResponse | Exception) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        answer = self._responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def close(self) -> None:
        self.closed = True


def test_fetch_decodes_by_the_pages_meta_charset() -> None:
    page = _NEWS_JA.replace('<?xml version="1.0" encoding="utf-8"?>\n', "").replace(
        "<head>", '<head><meta charset="shift_jis">'
    )
    response = _FakeResponse(page.encode("cp932"), url="https://www.example.com/neko")
    session = _FakeSession(response)

    article = fetch_article("https://www.example.com/neko", session=session)

    assert article.text == _ARTICLE_JA
    assert article.url == "https://www.example.com/neko"
    assert response.closed is True
    assert session.closed is False  # a caller's session stays open
    url, kwargs = session.calls[0]
    assert url == "https://www.example.com/neko"
    assert kwargs["stream"] is True and kwargs["timeout"] == (10, 30)


def test_fetch_prefers_the_header_charset_and_names_the_site_after_redirects() -> None:
    response = _FakeResponse(
        _NEWS_JA.encode("utf-8"), content_type="text/html; charset=UTF-8", url="https://news.example.jp/a/1"
    )

    article = fetch_article("https://example.jp/short", session=_FakeSession(response))

    assert article.site == "news.example.jp"
    assert article.text == _ARTICLE_JA


def test_fetch_without_any_charset_detects_utf8() -> None:
    page = _NEWS_JA.replace('<?xml version="1.0" encoding="utf-8"?>\n', "")
    article = fetch_article("https://example.com/a", session=_FakeSession(_FakeResponse(page.encode("utf-8"))))

    assert article.title == "猫の話"


@pytest.mark.parametrize(("status", "transient"), [(404, False), (403, False), (429, True), (503, True)])
def test_http_errors_say_whether_a_retry_can_help(status: int, transient: bool) -> None:
    session = _FakeSession(_FakeResponse(b"", status=status))

    with pytest.raises(ArticleFetchError) as excinfo:
        fetch_article("https://example.com/a", session=session)

    assert (excinfo.value.reason, excinfo.value.transient) == ("http_status", transient)
    assert str(excinfo.value) == f"https://example.com/a answered HTTP {status}."


def test_a_network_error_is_transient() -> None:
    session = _FakeSession(requests.ConnectionError("connection refused"))

    with pytest.raises(ArticleFetchError) as excinfo:
        fetch_article("https://example.com/a", session=session)

    assert (excinfo.value.reason, excinfo.value.transient) == ("network", True)


def test_a_file_that_is_not_a_web_page_is_refused() -> None:
    session = _FakeSession(_FakeResponse(b"%PDF-1.7", content_type="application/pdf"))

    with pytest.raises(ArticleFetchError) as excinfo:
        fetch_article("https://example.com/a.pdf", session=session)

    assert excinfo.value.reason == "not_html"
    assert str(excinfo.value) == "https://example.com/a.pdf is not a web page (application/pdf)."


def test_a_page_over_the_cap_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_article, "MAX_PAGE_BYTES", 1000)
    session = _FakeSession(_FakeResponse(_NEWS_JA.encode("utf-8")))

    with pytest.raises(ArticleFetchError) as excinfo:
        fetch_article("https://example.com/a", session=session)

    assert excinfo.value.reason == "too_large"


def test_an_invalid_address_never_reaches_the_session() -> None:
    session = _FakeSession()

    with pytest.raises(ArticleFetchError) as excinfo:
        fetch_article("example.com/a", session=session)

    assert excinfo.value.reason == "invalid_url"
    assert session.calls == []


def test_fetch_articles_keeps_going_after_a_failure() -> None:
    page = _NEWS_JA.encode("utf-8")
    session = _FakeSession(_FakeResponse(page), requests.Timeout("slow"), _FakeResponse(page))

    results = fetch_articles(["https://a.example/1", "https://b.example/2", "https://c.example/3"], session=session)

    assert [type(r).__name__ for r in results] == ["WebArticle", "ArticleFetchError", "WebArticle"]
    assert isinstance(results[1], ArticleFetchError) and results[1].url == "https://b.example/2"
