"""Boot-time recovery from a video-surface crash.

A GL-driver abort cannot be caught in-process, so the previous session leaves a
sentinel and THIS is the code that acts on it. Without it an affected user hits
the same crash on their very next video mine, because the curator is on the
mandatory path for all of them.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from anki_miner.gui.utils import runtime_state, video_preview


@pytest.fixture(autouse=True)
def _clean_state():
    video_preview._reset_for_tests()
    yield
    video_preview._reset_for_tests()


@pytest.fixture
def window(qtbot, patch_heavy_init, test_config, request):
    """A real MainWindow whose config carries the requested preview state."""
    enabled = getattr(request, "param", True)
    patch_heavy_init(replace(test_config, video_preview_enabled=enabled))
    from anki_miner.gui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    yield win
    win.deleteLater()


def _plant_marker(payload: str = '{"pid": 4242, "platform_name": "xcb"}') -> None:
    path = runtime_state.video_preview_marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


class TestMarkerPresent:
    def test_turns_the_preview_off_and_consumes_the_marker(self, window):
        _plant_marker()
        window._maybe_auto_disable_video_preview()
        assert window.config.video_preview_enabled is False
        assert not runtime_state.video_preview_marker_path().exists()

    def test_shows_a_banner_not_a_modal(self, window):
        """D24: a recoverable failure never gets a modal."""
        _plant_marker()
        window._maybe_auto_disable_video_preview()
        issue = window.issue_banner().current_issue()
        assert issue is not None
        assert issue.action_id == "video_preview.reenable"
        assert "4242" in issue.details

    def test_banner_action_reveals_the_setting(self, window, monkeypatch):
        revealed: list[str] = []
        monkeypatch.setattr(type(window), "reveal_setting", lambda _self, sid: revealed.append(sid))
        _plant_marker()
        window._maybe_auto_disable_video_preview()
        window.issue_banner().action_button.click()
        assert revealed == ["ui.video_preview"]

    def test_live_gate_follows_the_config_write(self, window):
        """update_config re-seeds the module state, so the very next curator in
        THIS session is already safe — not just after a restart."""
        video_preview.seed_from_config(window.config)
        assert video_preview.preview_enabled()
        _plant_marker()
        window._maybe_auto_disable_video_preview()
        assert not video_preview.preview_enabled()

    def test_corrupt_marker_still_disables(self, window):
        """The file's existence is the signal. A marker truncated by the very
        crash it records must not be the reason recovery does nothing."""
        _plant_marker('{"pid": 42')
        window._maybe_auto_disable_video_preview()
        assert window.config.video_preview_enabled is False
        assert window.issue_banner().current_issue() is not None


class TestAlreadyDisabled:
    @pytest.mark.parametrize("window", [False], indirect=True)
    def test_consumes_without_a_second_banner(self, window):
        """Saying it again to someone who already turned it off is noise — but
        the marker still has to go, or it re-fires every launch."""
        _plant_marker()
        window._maybe_auto_disable_video_preview()
        assert not runtime_state.video_preview_marker_path().exists()
        assert window.issue_banner().current_issue() is None


class TestNoMarker:
    def test_clean_boot_writes_nothing(self, window, monkeypatch):
        writes: list[object] = []
        monkeypatch.setattr(type(window), "update_config", lambda _self, cfg: writes.append(cfg))
        window._maybe_auto_disable_video_preview()
        assert writes == []
        assert window.config.video_preview_enabled is True
        assert window.issue_banner().current_issue() is None
