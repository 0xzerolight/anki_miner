"""F-14 (R17): Reading → Text fetches web articles into the editor; Mine keeps each page's Source.

No network and no thread: ``fetch_articles`` is replaced with canned results and
``run_off_thread`` runs the work inline, then fires ``on_finished`` the way the
real worker does. The queue worker class is patched as in
``test_reading_text_tab.py``.
"""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets import reading_text_tab
from anki_miner.gui.widgets.reading_text_tab import ReadingTextTab
from anki_miner.languages.registry import get_profile
from anki_miner.services.reading.web_article import ArticleFetchError, WebArticle

_WORKER_TARGET = "anki_miner.gui.widgets._reading_mining_base.ReadingQueueWorker"

_NEKO = WebArticle(
    url="https://example.com/neko", title="猫の話", site="example.com", text="吾輩は猫である。\n\n名前はまだ無い。"
)
_INU = WebArticle(url="https://example.org/inu", title="犬の話", site="example.org", text="犬が走る。")


def _inline_off_thread(dispatched: list):
    def _run(parent, work, on_done, on_error=None, **kwargs):
        dispatched.append(work)
        on_done(work())
        kwargs["on_finished"]()
        return MagicMock(name="Worker")

    return _run


def _build(qtbot, config: AnkiMinerConfig, monkeypatch: pytest.MonkeyPatch):
    dispatched: list = []
    monkeypatch.setattr(reading_text_tab, "run_off_thread", _inline_off_thread(dispatched))
    widget = ReadingTextTab(config=config, processor=MagicMock(), presenter=MagicMock())
    qtbot.addWidget(widget)
    widget._dispatched = dispatched  # type: ignore[attr-defined]
    return widget


@pytest.fixture
def tab(qtbot, test_config: AnkiMinerConfig, monkeypatch: pytest.MonkeyPatch):
    with patch(_WORKER_TARGET, autospec=False) as queue_cls:
        queue_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
        widget = _build(qtbot, test_config, monkeypatch)
        widget._queue_worker_cls = queue_cls  # type: ignore[attr-defined]
        yield widget


def _fetch(tab, monkeypatch: pytest.MonkeyPatch, urls: str, results: list) -> MagicMock:
    fake = MagicMock(return_value=results)
    monkeypatch.setattr(reading_text_tab, "fetch_articles", fake)
    tab.url_edit.setPlainText(urls)
    tab._on_fetch_clicked()
    return fake


def _items(tab) -> list:
    return tab._queue_worker_cls.call_args.kwargs["items"]


def _log(tab) -> str:
    return tab.log_widget.text_edit.toPlainText()


def test_fetch_puts_the_article_text_in_the_editor(tab, monkeypatch):
    fake = _fetch(tab, monkeypatch, "https://example.com/neko\n", [_NEKO])

    assert tab.text_edit.toPlainText() == _NEKO.text
    assert fake.call_args.args == (["https://example.com/neko"],)
    assert fake.call_args.kwargs["rules"] == get_profile("ja").sentence_rules
    assert "Fetched 猫の話 — example.com." in _log(tab)
    assert tab.fetch_button.isEnabled() and tab.fetch_button.text() == "Fetch"


def test_fetch_uses_the_mining_languages_rules(qtbot, test_config, monkeypatch):
    """Review Focus 3: a Spanish mining language fetches with Spanish line-join rules."""
    with patch(_WORKER_TARGET):
        tab = _build(qtbot, dataclasses.replace(test_config, language="es"), monkeypatch)
        fake = _fetch(tab, monkeypatch, "https://example.es/gato", [_NEKO])

    assert fake.call_args.kwargs["rules"] == get_profile("es").sentence_rules


def test_a_failed_page_is_logged_and_the_others_still_land(tab, monkeypatch):
    dead = ArticleFetchError("https://bad.example/x", "no_text", "No article text found at https://bad.example/x.")

    _fetch(tab, monkeypatch, "https://bad.example/x\nhttps://example.com/neko", [dead, _NEKO])

    assert tab.text_edit.toPlainText() == _NEKO.text
    assert (
        "Could not fetch https://bad.example/x: No article text found on this page. "
        "Pages that draw their text with JavaScript cannot be read."
    ) in _log(tab)
    assert tab.issue_banner().current_issue() is None


def test_when_every_page_fails_the_banner_says_why_and_the_editor_is_kept(tab, monkeypatch):
    tab.text_edit.setPlainText("元の文。")
    down = ArticleFetchError("https://example.com/a", "network", "Could not reach https://example.com/a: boom")

    _fetch(tab, monkeypatch, "https://example.com/a", [down])

    issue = tab.issue_banner().current_issue()
    assert issue.summary == "The page could not be reached. Check the address and your connection."
    assert issue.details == "Could not reach https://example.com/a: boom"
    assert tab.text_edit.toPlainText() == "元の文。"
    assert tab.fetch_button.isEnabled()


def test_several_failed_pages_share_one_banner(tab, monkeypatch):
    first = ArticleFetchError("https://a.example/1", "http_status", "https://a.example/1 answered HTTP 404.")
    second = ArticleFetchError("https://b.example/2", "not_html", "https://b.example/2 is not a web page (image/png).")

    _fetch(tab, monkeypatch, "https://a.example/1\nhttps://b.example/2", [first, second])

    assert tab.issue_banner().current_issue().summary == "None of the pages could be fetched."


def test_lines_that_are_not_web_addresses_are_refused(tab, monkeypatch):
    fake = _fetch(tab, monkeypatch, "https://example.com/a\nexample.com/b", [_NEKO])

    fake.assert_not_called()
    assert tab._dispatched == []
    issue = tab.issue_banner().current_issue()
    assert issue.summary == "Each line must be a web address that starts with http:// or https://."
    assert issue.details == "example.com/b"


def test_an_empty_address_box_is_refused(tab, monkeypatch):
    fake = _fetch(tab, monkeypatch, "  \n", [])

    fake.assert_not_called()
    assert tab.issue_banner().current_issue().summary == "Enter a web address first."


def test_more_than_ten_addresses_are_refused(tab, monkeypatch):
    """Fetch is an input step with no Cancel, so one Fetch takes at most ten pages."""
    urls = "\n".join(f"https://example.com/{n}" for n in range(11))

    fake = _fetch(tab, monkeypatch, urls, [])

    fake.assert_not_called()
    assert tab._dispatched == []
    assert tab.issue_banner().current_issue().summary == "Fetch takes up to 10 web addresses at a time."
    assert tab.fetch_button.isEnabled()


def test_an_unexpected_fetch_failure_is_reported(tab, monkeypatch):
    def _failing(parent, work, on_done, on_error=None, **kwargs):
        on_error("RuntimeError: parser crashed")
        kwargs["on_finished"]()

    monkeypatch.setattr(reading_text_tab, "run_off_thread", _failing)
    tab.url_edit.setPlainText("https://example.com/a")

    tab._on_fetch_clicked()

    issue = tab.issue_banner().current_issue()
    assert (issue.summary, issue.details) == ("The pages could not be fetched.", "RuntimeError: parser crashed")
    assert tab.fetch_button.isEnabled()


def test_mine_after_fetch_makes_one_item_per_article_with_its_source(tab, monkeypatch):
    _fetch(tab, monkeypatch, "https://example.com/neko\nhttps://example.org/inu", [_NEKO, _INU])

    tab._on_mine_clicked()

    items = _items(tab)
    assert [item.title for item in items] == ["猫の話 — example.com", "犬の話 — example.org"]
    assert [item.source.text for item in items] == [_NEKO.text, _INU.text]
    assert all(item.source.kind == "text" and item.source.path is None for item in items)


def test_an_edited_single_article_keeps_its_source(tab, monkeypatch):
    _fetch(tab, monkeypatch, "https://example.com/neko", [_NEKO])
    tab.text_edit.setPlainText("吾輩は猫である。")

    tab._on_mine_clicked()

    [item] = _items(tab)
    assert (item.title, item.source.text) == ("猫の話 — example.com", "吾輩は猫である。")


def test_edited_text_from_several_pages_is_mined_as_one_text(tab, monkeypatch):
    _fetch(tab, monkeypatch, "https://example.com/neko\nhttps://example.org/inu", [_NEKO, _INU])
    tab.text_edit.setPlainText("吾輩は猫である。犬が走る。")

    tab._on_mine_clicked()

    [item] = _items(tab)
    assert item.title == "Text"
    assert "The fetched text was edited, so it is mined as one text without its page titles." in _log(tab)


def test_clearing_the_editor_forgets_the_fetched_pages(tab, monkeypatch):
    _fetch(tab, monkeypatch, "https://example.com/neko", [_NEKO])
    tab.text_edit.clear()
    tab.text_edit.setPlainText("別の文。")

    tab._on_mine_clicked()

    [item] = _items(tab)
    assert item.title == "Text"


def test_pasted_text_still_mines_as_text(tab):
    tab.text_edit.setPlainText("今日は晴れ。")

    tab._on_mine_clicked()

    [item] = _items(tab)
    assert item.title == "Text"
