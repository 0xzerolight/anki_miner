"""Tests for the YouTube mining tab's state machine.

The tab's observable behaviour (button enabled/disabled, status text, Accept
visibility, resolved sub_mode) is driven entirely by :class:`_UIState`. We
exercise the state machine by feeding synthetic :class:`VideoInfo` values
into the probe-result handler and by invoking the worker signal slots
directly — actual yt-dlp / worker threads are never started.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from PyQt6.QtWidgets import QApplication

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.youtube_tab import YouTubeTab, _UIState
from anki_miner.models.youtube import VideoInfo

# QApplication instance needed for any widget test.
_app = QApplication.instance() or QApplication([])


def _make_video_info(
    *,
    video_id: str = "abc123",
    title: str = "Sample Video",
    duration_s: int = 600,
    has_manual_ja_subs: bool = False,
    has_auto_ja_subs: bool = False,
    thumbnail_url: str | None = None,
    uploader: str | None = "Uploader",
    is_live: bool = False,
    is_age_restricted: bool = False,
) -> VideoInfo:
    """Factory for :class:`VideoInfo` with sensible defaults."""
    return VideoInfo(
        video_id=video_id,
        title=title,
        duration_s=duration_s,
        has_manual_ja_subs=has_manual_ja_subs,
        has_auto_ja_subs=has_auto_ja_subs,
        thumbnail_url=thumbnail_url,
        uploader=uploader,
        is_live=is_live,
        is_age_restricted=is_age_restricted,
    )


@pytest.fixture
def tab(test_config: AnkiMinerConfig):
    """Instantiate a YouTubeTab with stub dependencies.

    ``processor`` and ``fetcher`` are plain ``object()`` sentinels — the
    tests never trigger a probe or a mine, so the tab's interactions with
    them are limited to attribute storage.
    """
    cfg = replace(
        test_config,
        youtube_max_duration_s=7200,
        youtube_cookies_from_browser=None,
    )
    widget = YouTubeTab(
        config=cfg,
        processor=object(),  # type: ignore[arg-type]
        fetcher=object(),  # type: ignore[arg-type]
        presenter=None,
    )
    yield widget
    widget.deleteLater()


class TestInitialState:
    """Initial state should be IDLE_NO_URL with Mine disabled."""

    def test_initial_state(self, tab):
        assert tab._state == _UIState.IDLE_NO_URL
        assert not tab.process_button.isEnabled()
        assert tab.status_label.text() == "Enter a YouTube URL and click Fetch Info."
        assert tab.accept_button.isHidden()


class TestProbeOutcomes:
    """Each VideoInfo shape lands the tab in the expected state."""

    def test_manual_subs_ready(self, tab):
        info = _make_video_info(has_manual_ja_subs=True)
        tab._on_probe_done(info)
        assert tab._state == _UIState.MANUAL_READY
        assert tab.process_button.isEnabled()
        assert tab._resolved_sub_mode == "manual_only"
        assert "ready to mine" in tab.status_label.text().lower()
        assert "Sample Video" in tab.metadata_label.text()

    def test_live_stream_blocks(self, tab):
        info = _make_video_info(is_live=True, has_manual_ja_subs=True)
        tab._on_probe_done(info)
        assert tab._state == _UIState.LIVE
        assert not tab.process_button.isEnabled()
        assert "live" in tab.status_label.text().lower()

    def test_age_restricted_without_cookies(self, tab):
        info = _make_video_info(is_age_restricted=True, has_manual_ja_subs=True)
        tab._on_probe_done(info)
        assert tab._state == _UIState.AGE_LOCKED
        assert not tab.process_button.isEnabled()
        assert "age" in tab.status_label.text().lower()

    def test_age_restricted_with_cookies_proceeds(self, test_config):
        cfg = replace(test_config, youtube_cookies_from_browser="firefox")
        widget = YouTubeTab(
            config=cfg,
            processor=object(),  # type: ignore[arg-type]
            fetcher=object(),  # type: ignore[arg-type]
            presenter=None,
        )
        try:
            info = _make_video_info(is_age_restricted=True, has_manual_ja_subs=True)
            widget._on_probe_done(info)
            assert widget._state == _UIState.MANUAL_READY
            assert widget.process_button.isEnabled()
        finally:
            widget.deleteLater()

    def test_age_restricted_with_cookies_auto_only(self, test_config):
        cfg = replace(test_config, youtube_cookies_from_browser="firefox")
        widget = YouTubeTab(
            config=cfg,
            processor=object(),  # type: ignore[arg-type]
            fetcher=object(),  # type: ignore[arg-type]
            presenter=None,
        )
        try:
            info = _make_video_info(is_age_restricted=True, has_auto_ja_subs=True)
            widget._on_probe_done(info)
            assert widget._state == _UIState.AUTO_PENDING
        finally:
            widget.deleteLater()

    def test_too_long(self, tab):
        info = _make_video_info(
            duration_s=tab._config.youtube_max_duration_s + 1,
            has_manual_ja_subs=True,
        )
        tab._on_probe_done(info)
        assert tab._state == _UIState.TOO_LONG
        assert not tab.process_button.isEnabled()
        assert "max duration" in tab.status_label.text().lower()

    def test_auto_only_pending(self, tab):
        info = _make_video_info(has_auto_ja_subs=True)
        tab._on_probe_done(info)
        assert tab._state == _UIState.AUTO_PENDING
        assert not tab.process_button.isEnabled()
        assert not tab.accept_button.isHidden()

    def test_auto_only_accept_arms_mine(self, tab):
        info = _make_video_info(has_auto_ja_subs=True)
        tab._on_probe_done(info)
        tab._on_accept_auto_clicked()
        assert tab._state == _UIState.AUTO_READY
        assert tab.process_button.isEnabled()
        assert tab._resolved_sub_mode == "auto_only"
        assert tab.accept_button.isHidden()

    def test_no_subs(self, tab):
        info = _make_video_info()
        tab._on_probe_done(info)
        assert tab._state == _UIState.NO_SUBS
        assert not tab.process_button.isEnabled()
        assert "no japanese subtitles" in tab.status_label.text().lower()

    def test_probe_error(self, tab):
        tab._on_probe_error("yt-dlp exploded")
        assert tab._state == _UIState.PROBE_ERROR
        assert not tab.process_button.isEnabled()
        assert "yt-dlp exploded" in tab.status_label.text()
        # Previous metadata must be cleared.
        assert tab.metadata_label.text() == ""


class TestMineLifecycleSlots:
    """Worker signal slots produce the right observable state."""

    def test_mine_error_reenables_mine(self, tab):
        # Pretend we had a valid mining session going.
        info = _make_video_info(has_manual_ja_subs=True)
        tab._on_probe_done(info)
        tab._transition(_UIState.MINING)
        tab.worker_thread = object()  # type: ignore[assignment]

        tab._on_mine_error("Bot detection triggered.")
        assert tab._state == _UIState.MINE_ERROR
        assert tab.process_button.isEnabled()
        assert "Bot detection triggered." in tab.status_label.text()

        # worker_thread handle clears when the QThread emits finished.
        tab._on_worker_finished()
        assert tab.worker_thread is None

    def test_mine_finished_shows_card_count(self, tab, monkeypatch):
        info = _make_video_info(has_manual_ja_subs=True)
        tab._on_probe_done(info)
        tab._transition(_UIState.MINING)
        tab.worker_thread = object()  # type: ignore[assignment]

        class _Result:
            cards_created = 7

        tab._on_mine_finished(_Result())
        assert tab._state == _UIState.MINED
        assert tab.process_button.isEnabled()
        assert "7 cards added" in tab.status_label.text()

        tab._on_worker_finished()
        assert tab.worker_thread is None

    def test_mine_progress_updates_status(self, tab):
        info = _make_video_info(has_manual_ja_subs=True)
        tab._on_probe_done(info)
        tab._transition(_UIState.MINING)

        tab._on_mine_progress("Downloading video", 42)
        assert "Downloading video" in tab.progress_widget.status_label.text()

        # Indeterminate flavor shouldn't crash.
        tab._on_mine_progress("Merging", -1)
        assert "Merging" in tab.progress_widget.status_label.text()


class TestUrlEditingResetsState:
    """Editing the URL invalidates a prior probe result."""

    def test_url_change_resets_video_info(self, tab):
        info = _make_video_info(has_manual_ja_subs=True)
        tab._on_probe_done(info)
        assert tab._video_info is not None

        tab.url_edit.setText("https://example.com/another")
        # textChanged fires synchronously in Qt's event loop; force via
        # explicit call to mirror the signal flow.
        assert tab._video_info is None
        assert not tab.process_button.isEnabled()


class TestActionButtons:
    """Preview Words + Process Video + Cancel — UX parity with anime tabs."""

    def test_buttons_present_and_disabled_at_startup(self, tab):
        assert tab.preview_button is not None
        assert tab.process_button is not None
        assert tab.cancel_button is not None
        assert not tab.preview_button.isEnabled()
        assert not tab.process_button.isEnabled()
        assert tab.cancel_button.isHidden()

    def test_manual_ready_enables_both_action_buttons(self, tab):
        info = _make_video_info(has_manual_ja_subs=True)
        tab._on_probe_done(info)
        assert tab._state == _UIState.MANUAL_READY
        assert tab.preview_button.isEnabled()
        assert tab.process_button.isEnabled()
        assert tab.cancel_button.isHidden()

    def test_mining_hides_actions_shows_cancel(self, tab):
        tab._transition(_UIState.MINING)
        assert not tab.cancel_button.isHidden()
        assert tab.preview_button.isHidden()
        assert tab.process_button.isHidden()

    def test_mined_restores_actions_hides_cancel(self, tab):
        tab._transition(_UIState.MINED, message="done")
        assert not tab.preview_button.isHidden()
        assert not tab.process_button.isHidden()
        assert tab.preview_button.isEnabled()
        assert tab.process_button.isEnabled()
        assert tab.cancel_button.isHidden()
