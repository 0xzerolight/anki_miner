"""Every screen answers a drop, one way or the other (decision D50-B).

Drops stay local to the screens that handle them -- a drag never teleports the
user somewhere they were not looking. What changes is that the eight screens
with no drop support at all now either take the payload or say why they cannot:

* YouTube takes one classified link and queues it through the ordinary add flow.
* Audio, Generate, Condense and Retime validate the file kind at the selector.
* Text refuses a dragged file instead of inserting its path as text.
* Card Backfill refuses everything, and points at the deck control it reads.

Deck Builder is deliberately absent: D3 (whether that screen exists at all) is
unresolved, so W5-T12 leaves it untouched.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QMimeData, QPointF, Qt, QUrl
from PyQt6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent

_YT_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _mime(*, urls: tuple[str, ...] = (), text: str | None = None) -> QMimeData:
    data = QMimeData()
    if urls:
        data.setUrls([QUrl(url) for url in urls])
    if text is not None:
        data.setText(text)
    return data


def _local(path) -> str:
    return QUrl.fromLocalFile(str(path)).toString()


def _enter_event(data: QMimeData) -> QDragEnterEvent:
    """Build a drag-enter event. The CALLER must keep ``data`` alive: the event
    holds a borrowed pointer, and letting the mime die first segfaults Qt."""
    return QDragEnterEvent(
        QPointF(1.0, 1.0).toPoint(),
        Qt.DropAction.CopyAction,
        data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _drop_event(data: QMimeData) -> QDropEvent:
    """Build a drop event. Same borrowed-mime rule as :func:`_enter_event`."""
    return QDropEvent(
        QPointF(1.0, 1.0),
        Qt.DropAction.CopyAction,
        data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _enter(widget, data: QMimeData) -> QDragEnterEvent:
    event = _enter_event(data)
    widget.dragEnterEvent(event)
    return event


def _drop(widget, data: QMimeData) -> QDropEvent:
    event = _drop_event(data)
    widget.dropEvent(event)
    return event


# ---------------------------------------------------------------------------
# YouTube: one link, added the ordinary way
# ---------------------------------------------------------------------------


@pytest.fixture
def youtube_tab(qtbot, test_config):
    from anki_miner.gui.widgets.youtube_tab import YouTubeTab

    cfg = replace(test_config, youtube_max_duration_s=7200, youtube_cookies_from_browser=None)
    with (
        patch("anki_miner.gui.widgets.youtube_tab.YouTubeQueueWorker") as queue_cls,
        patch("anki_miner.gui.widgets.youtube_playlist_flow.YouTubeProbeWorker"),
        patch("anki_miner.gui.widgets.youtube_playlist_flow.YouTubePlaylistResolveWorker"),
        patch("anki_miner.gui.widgets.youtube_playlist_flow.YouTubePlaylistProbeWorker"),
    ):
        queue_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
        widget = YouTubeTab(config=cfg, processor=MagicMock(), fetcher=MagicMock(), presenter=MagicMock())
        qtbot.addWidget(widget)
        yield widget
        widget.deleteLater()


class TestYouTubeTakesALink:
    def test_a_link_lights_the_url_box(self, youtube_tab):
        _enter(youtube_tab, _mime(urls=(_YT_URL,)))

        assert youtube_tab.url_edit.property("dropState") == "valid"

    def test_a_dropped_link_goes_into_the_add_flow(self, youtube_tab):
        youtube_tab._add_flow = MagicMock()

        event = _drop(youtube_tab, _mime(urls=(_YT_URL,)))

        youtube_tab._add_flow.begin.assert_called_once_with(_YT_URL)
        assert event.isAccepted()

    def test_a_link_dragged_as_plain_text_counts(self, youtube_tab):
        youtube_tab._add_flow = MagicMock()

        _drop(youtube_tab, _mime(text=_YT_URL))

        youtube_tab._add_flow.begin.assert_called_once_with(_YT_URL)

    def test_a_non_youtube_url_is_marked_invalid_and_refused(self, youtube_tab):
        youtube_tab._add_flow = MagicMock()

        _enter(youtube_tab, _mime(urls=("https://example.com/video",)))
        event = _drop(youtube_tab, _mime(urls=("https://example.com/video",)))

        assert youtube_tab.url_edit.property("dropState") == ""  # cleared on drop
        youtube_tab._add_flow.begin.assert_not_called()
        assert event.isAccepted() is False

    def test_a_dropped_file_says_where_files_are_mined(self, youtube_tab, tmp_path):
        episode = tmp_path / "ep01.mkv"
        episode.touch()
        youtube_tab._add_flow = MagicMock()

        _drop(youtube_tab, _mime(urls=(_local(episode),)))

        logged = youtube_tab.log_widget.text_edit.toPlainText()
        assert "Video and Audio tabs" in logged
        youtube_tab._add_flow.begin.assert_not_called()

    def test_a_queue_reorder_drag_is_left_to_the_list(self, youtube_tab):
        """An internal move carries neither URL nor text and is not this screen's."""
        event = _enter(youtube_tab, _mime())

        assert youtube_tab.url_edit.property("dropState") in (None, "")
        assert event.isAccepted() is False

    def test_the_light_goes_out_when_the_drag_leaves(self, youtube_tab):
        _enter(youtube_tab, _mime(urls=(_YT_URL,)))

        youtube_tab.dragLeaveEvent(QDragLeaveEvent())

        assert youtube_tab.url_edit.property("dropState") == ""


# ---------------------------------------------------------------------------
# Text: files are refused, text is not
# ---------------------------------------------------------------------------


@pytest.fixture
def text_tab(qtbot, test_config):
    from anki_miner.gui.widgets.reading_text_tab import ReadingTextTab

    with patch("anki_miner.gui.widgets._reading_mining_base.ReadingQueueWorker"):
        widget = ReadingTextTab(config=test_config, processor=MagicMock(), presenter=MagicMock())
        qtbot.addWidget(widget)
        yield widget
        widget.deleteLater()


class TestTextRefusesFiles:
    def test_a_dragged_file_never_reaches_the_editor(self, text_tab, tmp_path):
        novel = tmp_path / "book.epub"
        novel.touch()
        data = _mime(urls=(_local(novel),))
        event = _enter_event(data)

        eaten = text_tab._file_drop_filter.eventFilter(text_tab.text_edit, event)

        assert eaten is True
        assert event.isAccepted() is False

    def test_dropping_a_file_states_the_reason(self, text_tab, tmp_path):
        novel = tmp_path / "book.epub"
        novel.touch()
        data = _mime(urls=(_local(novel),))
        event = _drop_event(data)

        text_tab._file_drop_filter.eventFilter(text_tab.text_edit, event)

        assert "files are not supported" in text_tab.log_widget.text_edit.toPlainText()
        assert text_tab.text_edit.toPlainText() == ""

    def test_a_text_drag_passes_straight_through(self, text_tab):
        data = _mime(text="毎日ご飯を食べた。")
        event = _enter_event(data)

        eaten = text_tab._file_drop_filter.eventFilter(text_tab.text_edit, event)

        assert eaten is False


# ---------------------------------------------------------------------------
# Card Backfill: nothing to drop, and it says so
# ---------------------------------------------------------------------------


@pytest.fixture
def backfill_tab(qtbot, test_config):
    from anki_miner.gui.widgets.backfill_tab import CardBackfillTab

    widget = CardBackfillTab(test_config)
    qtbot.addWidget(widget)
    return widget


class TestBackfillRefusesEverything:
    def test_the_reason_appears_while_the_drag_is_still_in_the_air(self, backfill_tab, tmp_path):
        episode = tmp_path / "ep01.mkv"
        episode.touch()

        _enter(backfill_tab, _mime(urls=(_local(episode),)))

        assert backfill_tab.status_label.text() == "Card Backfill works on the selected Anki deck."

    def test_the_drop_is_refused_and_points_at_the_deck_control(self, backfill_tab, tmp_path):
        episode = tmp_path / "ep01.mkv"
        episode.touch()

        event = _drop(backfill_tab, _mime(urls=(_local(episode),)))

        assert event.isAccepted() is False
        assert backfill_tab.status_label.text() == "Card Backfill works on the selected Anki deck."
        assert backfill_tab.focusWidget() is backfill_tab.deck_combo

    def test_the_reason_is_taken_back_down_when_the_drag_leaves(self, backfill_tab, tmp_path):
        episode = tmp_path / "ep01.mkv"
        episode.touch()
        _enter(backfill_tab, _mime(urls=(_local(episode),)))

        backfill_tab.dragLeaveEvent(QDragLeaveEvent())

        assert backfill_tab.status_label.text() == ""


# ---------------------------------------------------------------------------
# The four selector screens: the file kind is checked at the field
# ---------------------------------------------------------------------------


def _tool_selectors():
    """(builder, selector attribute, good suffix, wrong suffix) per screen."""

    def audiobook(qtbot, test_config):
        from anki_miner.gui.widgets.audiobook_tab import AudiobookTab

        with patch("anki_miner.gui.widgets.audiobook_tab.AudiobookQueueWorker"):
            widget = AudiobookTab(config=test_config, processor=MagicMock(), presenter=MagicMock())
        qtbot.addWidget(widget)
        return widget

    def condense(qtbot, test_config):
        from anki_miner.gui.widgets.condense_tab import CondenseTab

        widget = CondenseTab(config=test_config)
        qtbot.addWidget(widget)
        return widget

    def retime(qtbot, test_config):
        from anki_miner.gui.widgets.subtitle_retime_tab import SubtitleRetimeTab

        widget = SubtitleRetimeTab(config=test_config)
        qtbot.addWidget(widget)
        return widget

    def generate(qtbot, test_config):
        from anki_miner.gui.widgets.subtitle_creation_tab import SubtitleCreationTab

        widget = SubtitleCreationTab(config=test_config)
        qtbot.addWidget(widget)
        return widget

    return [
        pytest.param(audiobook, "audio_selector", ".m4b", ".srt", id="audio-audio-field"),
        pytest.param(audiobook, "subtitle_selector", ".srt", ".m4b", id="audio-subtitle-field"),
        pytest.param(condense, "media_file_selector", ".mkv", ".srt", id="condense-media-field"),
        pytest.param(condense, "subtitle_file_selector", ".ass", ".mkv", id="condense-subtitle-field"),
        pytest.param(retime, "video_file_selector", ".mkv", ".srt", id="retime-video-field"),
        pytest.param(retime, "subtitle_file_selector", ".srt", ".mkv", id="retime-subtitle-field"),
        pytest.param(generate, "file_selector", ".mkv", ".srt", id="generate-video-field"),
    ]


@pytest.mark.parametrize(("build", "attribute", "good", "wrong"), _tool_selectors())
class TestTheToolScreensValidateAtTheField:
    def test_the_right_kind_lands(self, qtbot, test_config, tmp_path, build, attribute, good, wrong):
        selector = getattr(build(qtbot, test_config), attribute)
        accepted = tmp_path / f"ep01{good}"
        accepted.touch()

        _drop(selector, _mime(urls=(_local(accepted),)))

        assert selector.get_path() == str(accepted)

    def test_the_wrong_kind_is_refused_with_a_reason(self, qtbot, test_config, tmp_path, build, attribute, good, wrong):
        selector = getattr(build(qtbot, test_config), attribute)
        rejected = tmp_path / f"ep01{wrong}"
        rejected.touch()

        _drop(selector, _mime(urls=(_local(rejected),)))

        assert selector.get_path() == ""
        assert "takes a" in selector.status_label.text()

    def test_a_valid_drag_lights_the_field(self, qtbot, test_config, tmp_path, build, attribute, good, wrong):
        selector = getattr(build(qtbot, test_config), attribute)
        accepted = tmp_path / f"ep01{good}"
        accepted.touch()

        _enter(selector, _mime(urls=(_local(accepted),)))

        assert selector.input.property("dropState") == "valid"
