"""Saving on close and offering on launch — the whole D16-C loop end to end.

Two things are proven here that the per-screen tests cannot: the window finds
every queue screen without a hand-kept list (a queue can live two containers
deep), and the startup path asks exactly once, after the tabs exist, restoring
or discarding according to the answer — and never *running* anything either way.
"""

from __future__ import annotations

import hashlib
import json
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.gui import app as app_module
from anki_miner.gui.utils import queue_state_store as store
from anki_miner.gui.utils import runtime_state
from anki_miner.gui.utils.queue_state_store import QueueItemSnapshot, QueueSnapshot
from anki_miner.services.download_resume import ResumeManifest, ResumeState


@pytest.fixture()
def _home(tmp_path, monkeypatch):
    from anki_miner.config import paths
    from anki_miner.gui.utils.config_manager import GUIConfigManager

    home = tmp_path / "home"
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", home / "gui_config.json")
    monkeypatch.setattr(paths, "ANKI_MINER_HOME", home)
    return home


@pytest.fixture()
def window(qtbot, patch_heavy_init, test_config):
    patch_heavy_init(test_config)
    from anki_miner.gui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    yield win
    win.deleteLater()


class _Screen:
    """A stand-in queue screen: it only has to describe and take back rows."""

    def __init__(self, key, items=()):
        self.QUEUE_STATE_KEY = key
        self._items = tuple(items)
        self.restored: list[QueueSnapshot] = []

    def queue_snapshot(self):
        return QueueSnapshot(key=self.QUEUE_STATE_KEY, items=self._items)

    def restore_queue_snapshot(self, snapshot):
        self.restored.append(snapshot)
        return len(snapshot.items)


def _row(index):
    return QueueItemSnapshot(
        item_id=f"row-{index}",
        source=store.url_source(f"https://youtu.be/{index}"),
        title=f"Ep {index}",
    )


class TestScreenDiscovery:
    def test_a_screen_two_containers_deep_is_still_found(self, _home, window, qtbot):
        """Video nests Batch and YouTube; Reading nests four more."""
        from PyQt6.QtWidgets import QWidget

        outer = QWidget(window)
        middle = QWidget(outer)
        deep = QWidget(middle)
        deep.QUEUE_STATE_KEY = "queue.deep"  # type: ignore[attr-defined]
        shallow = QWidget(window)
        shallow.QUEUE_STATE_KEY = "queue.shallow"  # type: ignore[attr-defined]

        keys = {screen.QUEUE_STATE_KEY for screen in window.iter_queue_screens()}
        assert {"queue.deep", "queue.shallow"} <= keys

    def test_a_widget_with_no_key_is_never_asked(self, _home, window, qtbot):
        from PyQt6.QtWidgets import QWidget

        QWidget(window)
        assert all(getattr(screen, "QUEUE_STATE_KEY", None) for screen in window.iter_queue_screens())

    def test_every_queue_owning_tab_class_declares_a_distinct_key(self):
        from anki_miner.gui.widgets.audiobook_tab import AudiobookTab
        from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
        from anki_miner.gui.widgets.reading_subtitles_tab import ReadingSubtitlesTab
        from anki_miner.gui.widgets.youtube_tab import YouTubeTab

        keys = [
            AudiobookTab.QUEUE_STATE_KEY,
            BatchProcessingTab.QUEUE_STATE_KEY,
            ReadingSubtitlesTab.QUEUE_STATE_KEY,
            YouTubeTab.QUEUE_STATE_KEY,
        ]
        assert len(set(keys)) == len(keys)
        assert all(runtime_state.validate_key(key) for key in keys)


class TestSaveAndRestore:
    def _stub(self, window, *screens):
        window.iter_queue_screens = lambda: list(screens)  # type: ignore[method-assign]

    def test_closing_writes_one_snapshot_per_screen(self, _home, window):
        self._stub(window, _Screen("queue.youtube", (_row(0), _row(1))), _Screen("queue.audiobook", (_row(2),)))
        window.save_queue_snapshots()
        assert set(store.stored_keys()) == {"queue.youtube", "queue.audiobook"}
        loaded = store.load("queue.youtube")
        assert loaded is not None
        assert [item.item_id for item in loaded.items] == ["row-0", "row-1"]

    def test_a_screen_that_cannot_describe_itself_does_not_stop_the_others(self, _home, window):
        broken = _Screen("queue.batch")
        broken.queue_snapshot = MagicMock(side_effect=RuntimeError("boom"))
        self._stub(window, broken, _Screen("queue.youtube", (_row(0),)))
        window.save_queue_snapshots()
        assert store.stored_keys() == ("queue.youtube",)

    def test_restore_hands_each_screen_only_its_own_snapshot(self, _home, window):
        youtube = _Screen("queue.youtube", (_row(0), _row(1)))
        audiobook = _Screen("queue.audiobook", (_row(2),))
        self._stub(window, youtube, audiobook)
        window.save_queue_snapshots()

        fresh_youtube = _Screen("queue.youtube")
        fresh_audiobook = _Screen("queue.audiobook")
        self._stub(window, fresh_youtube, fresh_audiobook)
        assert window.restore_queue_snapshots() == 3
        assert [s.key for s in fresh_youtube.restored] == ["queue.youtube"]
        assert [s.key for s in fresh_audiobook.restored] == ["queue.audiobook"]

    def test_a_screen_that_fails_to_restore_does_not_stop_the_others(self, _home, window):
        self._stub(window, _Screen("queue.youtube", (_row(0),)), _Screen("queue.audiobook", (_row(1),)))
        window.save_queue_snapshots()

        broken = _Screen("queue.youtube")
        broken.restore_queue_snapshot = MagicMock(side_effect=RuntimeError("boom"))
        good = _Screen("queue.audiobook")
        self._stub(window, broken, good)
        assert window.restore_queue_snapshots() == 1
        assert len(good.restored) == 1

    def test_nothing_is_restored_when_nothing_was_saved(self, _home, window):
        screen = _Screen("queue.youtube")
        self._stub(window, screen)
        assert window.restore_queue_snapshots() == 0
        assert screen.restored == []


def _seed_partial():
    state = ResumeState(runtime_state.download_resume_root(), "resource-dict-jmdict")
    state.ensure_root()
    body = b"x" * (4 * 1024 * 1024)
    state.part_path.write_bytes(body)
    state.manifest_path.write_text(
        json.dumps(
            ResumeManifest(
                url="https://example.com/jmdict.zip",
                total=8 * 1024 * 1024,
                length=len(body),
                sha256=hashlib.sha256(body).hexdigest(),
                etag='"v1"',
                last_modified=None,
            ).to_json()
        ),
        encoding="utf-8",
    )
    return state


class TestStartupOffer:
    def test_nothing_to_offer_asks_nothing(self, _home, window):
        with patch.object(app_module.RecoveryController, "offer") as offer:
            assert app_module.offer_recovery(window) is False
        offer.assert_not_called()

    def test_restore_refills_the_queues_and_keeps_the_partial(self, _home, window):
        state = _seed_partial()
        screen = _Screen("queue.youtube", (_row(0), _row(1)))
        window.iter_queue_screens = lambda: [screen]  # type: ignore[method-assign]
        window.save_queue_snapshots()

        fresh = _Screen("queue.youtube")
        window.iter_queue_screens = lambda: [fresh]  # type: ignore[method-assign]
        with patch.object(app_module.RecoveryController, "offer", return_value=True):
            assert app_module.offer_recovery(window) is True

        assert len(fresh.restored) == 1
        # Restore keeps the bytes; whether they may be APPENDED to is decided
        # later, against the server's own validators.
        assert state.part_path.exists()
        assert state.manifest_path.exists()

    def test_discard_removes_both_kinds_of_state_and_restores_nothing(self, _home, window):
        state = _seed_partial()
        screen = _Screen("queue.youtube", (_row(0),))
        window.iter_queue_screens = lambda: [screen]  # type: ignore[method-assign]
        window.save_queue_snapshots()

        fresh = _Screen("queue.youtube")
        window.iter_queue_screens = lambda: [fresh]  # type: ignore[method-assign]
        with patch.object(app_module.RecoveryController, "offer", return_value=False):
            assert app_module.offer_recovery(window) is False

        assert fresh.restored == []
        assert not state.part_path.exists()
        assert store.stored_keys() == ()

    def test_a_restored_queue_starts_no_work(self, _home, window):
        """The whole point of D16-C's 'never automatically'."""
        screen = _Screen("queue.youtube", (_row(0), _row(1)))
        window.iter_queue_screens = lambda: [screen]  # type: ignore[method-assign]
        window.save_queue_snapshots()

        fresh = _Screen("queue.youtube")
        started: list[object] = []
        fresh.start_run = lambda *a, **kw: started.append(a)  # type: ignore[attr-defined]
        window.iter_queue_screens = lambda: [fresh]  # type: ignore[method-assign]
        with patch.object(app_module.RecoveryController, "offer", return_value=True):
            app_module.offer_recovery(window)
        assert started == []
        # No live task was published either — restoring is not running.
        assert window.task_registry.running() == ()
