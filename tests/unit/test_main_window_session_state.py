"""MainWindow resumes the geometry and route the last session ended on (D7).

Two halves are pinned here. On close, exactly one write of the *stable* keys —
never a stack index, never a translated label. On open, ``restoreGeometry`` is
left as the sole authority for a usable blob (it owns maximised state and the
"that monitor is gone" relocation), with a centred 1280x800 default reserved for
an absent or unusable one.

What is NOT restored matters as much: no scroll offset and no form draft ever
reach the file, so every page opens at the top with whatever its config says.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QByteArray, QEvent, QRect, QSettings
from PyQt6.QtWidgets import QApplication, QScrollArea

from anki_miner.gui.constants import WINDOW_DEFAULT_HEIGHT, WINDOW_DEFAULT_WIDTH
from anki_miner.gui.utils import session_state


@pytest.fixture
def main_window(qtbot, patch_heavy_init, test_config):
    patch_heavy_init(test_config)
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    yield window
    window.deleteLater()


def _trigger_close(window) -> MagicMock:
    event = MagicMock(spec=QEvent)
    window.closeEvent(event)
    return event


# ---------------------------------------------------------------------------
# Geometry round trip
# ---------------------------------------------------------------------------


class TestGeometryRoundTrip:
    def test_normal_geometry_survives_a_close_and_reopen(self, main_window, qtbot):
        main_window.show()
        qtbot.waitExposed(main_window)
        main_window.setGeometry(QRect(60, 40, 1100, 780))
        QApplication.processEvents()
        expected = main_window.saveGeometry()

        _trigger_close(main_window)

        assert session_state.load_geometry() == expected
        # And the blob really is usable by the API that will consume it.
        assert main_window.restoreGeometry(session_state.load_geometry())

    def test_maximised_state_survives_a_close_and_reopen(self, main_window, qtbot):
        main_window.show()
        qtbot.waitExposed(main_window)
        main_window.showMaximized()
        QApplication.processEvents()

        _trigger_close(main_window)

        blob = session_state.load_geometry()
        assert blob is not None
        main_window.showNormal()
        QApplication.processEvents()
        assert main_window.restoreGeometry(blob)
        assert main_window.isMaximized()

    def test_a_successful_maximised_restore_is_not_overridden(self, main_window, qtbot, monkeypatch):
        """setGeometry after a good restore would un-maximise the window."""
        main_window.show()
        qtbot.waitExposed(main_window)
        main_window.showMaximized()
        QApplication.processEvents()
        _trigger_close(main_window)
        main_window.showNormal()
        QApplication.processEvents()

        calls: list[object] = []
        monkeypatch.setattr(type(main_window), "setGeometry", lambda self, *a: calls.append(a), raising=False)

        main_window.restore_session_state()

        assert calls == []


# ---------------------------------------------------------------------------
# Off-screen and corrupt blobs
# ---------------------------------------------------------------------------


class TestGeometryFallback:
    def test_offscreen_geometry_is_relocated_onto_an_available_screen(self, main_window, qtbot, monkeypatch):
        """A window saved on a monitor that is gone comes back reachable.

        Qt owns this: ``restoreGeometry`` relocates a valid-but-unreachable
        blob itself, which is why the blob is only ever discarded when
        restoration *fails*.
        """
        available = QApplication.primaryScreen().availableGeometry()
        main_window.show()
        qtbot.waitExposed(main_window)
        main_window.setGeometry(QRect(available.right() + 4000, available.bottom() + 3000, 1100, 780))
        QApplication.processEvents()
        _trigger_close(main_window)
        assert session_state.load_geometry() is not None

        requested = _spy_on_set_geometry(main_window, monkeypatch)
        main_window.restore_session_state()
        QApplication.processEvents()

        # Non-vacuity: this is the restore path, not the centred-default path.
        assert requested == []
        assert main_window.frameGeometry().intersects(
            available
        ), f"restored at {main_window.frameGeometry()}, entirely outside {available}"

    def test_corrupt_blob_falls_back_to_the_centred_default(self, main_window, monkeypatch):
        session_state.save_geometry(QByteArray(b"\x01\x02 not a geometry blob"))
        requested = _spy_on_set_geometry(main_window, monkeypatch)

        main_window.restore_session_state()

        assert requested == [_centred_default()]

    def test_absent_blob_falls_back_to_the_centred_default(self, main_window, monkeypatch):
        assert session_state.load_geometry() is None
        requested = _spy_on_set_geometry(main_window, monkeypatch)

        main_window.restore_session_state()

        assert requested == [_centred_default()]

    def test_the_default_lands_on_the_available_screen(self, main_window):
        """Behavioural counterpart: the fallback is never off-screen."""
        main_window.restore_session_state()

        assert main_window.geometry().intersects(QApplication.primaryScreen().availableGeometry())


def _centred_default() -> QRect:
    """1280x800, clamped to the screen's available area, centred in it."""
    available = QApplication.primaryScreen().availableGeometry()
    rect = QRect(
        0,
        0,
        min(WINDOW_DEFAULT_WIDTH, available.width()),
        min(WINDOW_DEFAULT_HEIGHT, available.height()),
    )
    rect.moveCenter(available.center())
    return rect


def _spy_on_set_geometry(window, monkeypatch) -> list[QRect]:
    """Record what the window ASKS for.

    The resulting geometry cannot be asserted directly: ``setMinimumSize``
    floors it, so on a display narrower than the 1024px minimum (the offscreen
    platform is 800px) Qt widens the window back out and defeats the centring.
    The request is the contract; Qt's clamping is Qt's business.
    """
    requested: list[QRect] = []
    monkeypatch.setattr(type(window), "setGeometry", lambda _self, rect: requested.append(rect), raising=False)
    return requested


# ---------------------------------------------------------------------------
# Route: stable keys only
# ---------------------------------------------------------------------------


class TestRouteRoundTrip:
    def test_main_tab_and_every_container_subtab_are_saved(self, wired_window):
        window, _titles, _tabs = wired_window
        window.reveal_capability(_target("video", "youtube"))
        window.reveal_capability(_target("reading", "novels"))
        window.reveal_capability(_target("subtitles", "condense"))
        window.reveal_capability(_target("settings", "filtering"))
        window.reveal_capability(_target("reading", "novels"))

        _trigger_close(window)

        main_tab, subtabs = session_state.load_route()
        assert main_tab == "reading"
        # Every container, not only the one on show when the window closed.
        assert subtabs == {
            "video": "youtube",
            "reading": "novels",
            "subtitles": "condense",
            "settings": "filtering",
        }

    def test_route_is_restored_onto_the_right_tabs(self, wired_window):
        window, _titles, _tabs = wired_window
        session_state.save_route("subtitles", {"video": "batch", "subtitles": "retime"})

        window.restore_session_state()

        assert window._current_main_tab_key() == "subtitles"
        assert window._current_subtab_keys()["video"] == "batch"
        assert window._current_subtab_keys()["subtitles"] == "retime"

    def test_a_deck_builder_route_survives_although_it_has_no_subtabs(self, wired_window):
        window, _titles, _tabs = wired_window
        session_state.save_route("deckbuilder", {})

        window.restore_session_state()

        assert window._current_main_tab_key() == "deckbuilder"

    def test_unknown_keys_are_ignored(self, wired_window):
        window, _titles, _tabs = wired_window
        before = window.tabs.currentIndex()
        session_state.save_route("no-such-tab", {"video": "no-such-subtab"})

        window.restore_session_state()

        assert window.tabs.currentIndex() == before
        assert window._current_subtab_keys()["video"] == "single"

    def test_no_scroll_offset_is_ever_persisted(self, wired_window):
        """A three-day-old scroll position is not something to resume (D7)."""
        window, _titles, _tabs = wired_window
        window.reveal_capability(_target("settings", "filtering"))
        page = window.tabs.currentWidget().pages.currentWidget()
        assert isinstance(page, QScrollArea)  # vacuity guard
        page.verticalScrollBar().setValue(page.verticalScrollBar().maximum())

        _trigger_close(window)

        settings = QSettings(str(session_state.state_file()), QSettings.Format.IniFormat)
        assert not [key for key in settings.allKeys() if "scroll" in key.lower()]

    def test_restored_pages_start_at_the_top(self, wired_window):
        window, _titles, _tabs = wired_window
        session_state.save_route("settings", {"settings": "filtering"})

        window.restore_session_state()

        for area in window.tabs.currentWidget().findChildren(QScrollArea):
            bar = area.verticalScrollBar()
            assert bar is None or bar.value() == 0


def _target(main_tab: str, subtab: str | None = None):
    from anki_miner.gui.capabilities import CapabilityTarget

    return CapabilityTarget(main_tab, subtab)


# ---------------------------------------------------------------------------
# Save discipline
# ---------------------------------------------------------------------------


class TestSaveDiscipline:
    def test_session_state_is_saved_before_background_shutdown(self, main_window, monkeypatch):
        """shutdown may hide the window; a hidden window's geometry is wrong."""
        order: list[str] = []
        monkeypatch.setattr(session_state, "save_geometry", lambda _blob: order.append("save_geometry"))
        original = main_window.background_tasks.shutdown
        monkeypatch.setattr(
            main_window.background_tasks,
            "shutdown",
            lambda tabs: order.append("shutdown") or original(tabs),
        )

        _trigger_close(main_window)

        assert order[0] == "save_geometry"
        assert "shutdown" in order

    def test_repeated_close_attempts_save_once(self, main_window, monkeypatch):
        saves: list[object] = []
        monkeypatch.setattr(session_state, "save_geometry", lambda blob: saves.append(blob))

        _trigger_close(main_window)
        _trigger_close(main_window)
        _trigger_close(main_window)

        assert len(saves) == 1

    def test_a_write_failure_still_lets_the_window_close(self, main_window, monkeypatch):
        def boom(*_a, **_kw):
            raise OSError("read-only home")

        monkeypatch.setattr(session_state, "save_geometry", boom)

        event = _trigger_close(main_window)

        event.accept.assert_called_once()
