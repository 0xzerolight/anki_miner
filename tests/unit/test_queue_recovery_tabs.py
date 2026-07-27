"""Queue screens describing themselves durably, and coming back (D16-C).

One test file rather than four additions because the contract is one contract:
a screen with a ``QUEUE_STATE_KEY`` can hand over its rows and take them back in
the same order, with the same ids, and a row that was mid-run comes back saying
so and does **not** run again on its own.

Workers are never started here. Every screen is constructed with its worker class
patched, exactly as its own test module does.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils import queue_state_store as store
from anki_miner.gui.widgets.audiobook_tab import AudiobookTab
from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.gui.widgets.reading_subtitles_tab import ReadingSubtitlesTab
from anki_miner.gui.widgets.youtube_tab import YouTubeTab
from anki_miner.models.batch_queue import QueueItemStatus
from anki_miner.models.mining_queue import ReadyItemStatus
from anki_miner.models.youtube_queue import YouTubeItemStatus


@pytest.fixture()
def _home(tmp_path, monkeypatch):
    from anki_miner.config import paths
    from anki_miner.gui.utils.config_manager import GUIConfigManager

    home = tmp_path / "home"
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", home / "gui_config.json")
    monkeypatch.setattr(paths, "ANKI_MINER_HOME", home)
    return home


def _pair(tmp_path: Path, stem: str) -> tuple[Path, Path]:
    audio = tmp_path / f"{stem}.m4b"
    subtitle = tmp_path / f"{stem}.srt"
    audio.write_bytes(b"a")
    subtitle.write_text("1\n")
    return audio, subtitle


@pytest.fixture()
def audiobook(qtbot, test_config: AnkiMinerConfig):
    with patch("anki_miner.gui.widgets.audiobook_tab.AudiobookQueueWorker") as cls:
        cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
        widget = AudiobookTab(config=test_config, processor=MagicMock(), presenter=MagicMock())
        qtbot.addWidget(widget)
        yield widget
        widget.deleteLater()


@pytest.fixture()
def youtube(qtbot, test_config: AnkiMinerConfig):
    with patch("anki_miner.gui.widgets.youtube_tab.YouTubeQueueWorker") as cls:
        cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
        widget = YouTubeTab(
            config=test_config,
            processor=MagicMock(),
            fetcher=MagicMock(name="Fetcher"),
            presenter=MagicMock(),
        )
        qtbot.addWidget(widget)
        yield widget
        widget.deleteLater()


@pytest.fixture()
def batch(qtbot, test_config: AnkiMinerConfig):
    widget = BatchProcessingTab(config=test_config, presenter=MagicMock(), progress_callback=MagicMock())
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


@pytest.fixture()
def reading_subtitles(qtbot, test_config: AnkiMinerConfig):
    widget = ReadingSubtitlesTab(config=test_config, processor=MagicMock(), presenter=MagicMock())
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


class TestAudiobook:
    def test_the_queue_round_trips_in_order_with_its_ids(self, _home, audiobook, tmp_path):
        ids = []
        for stem in ("a", "b", "c"):
            item = audiobook._queue.add(*_pair(tmp_path, stem))
            ids.append(item.item_id)
        snapshot = audiobook.queue_snapshot()
        store.save(snapshot)

        audiobook._queue._items.clear()
        assert audiobook.restore_queue_snapshot(store.load(audiobook.QUEUE_STATE_KEY)) == 3
        restored = audiobook._queue.all_items()
        assert [item.item_id for item in restored] == ids
        assert [item.audio_file.name for item in restored] == ["a.m4b", "b.m4b", "c.m4b"]
        assert all(item.status is ReadyItemStatus.READY for item in restored)

    def test_a_row_that_was_running_comes_back_interrupted_and_not_ready(self, _home, audiobook, tmp_path):
        item = audiobook._queue.add(*_pair(tmp_path, "a"))
        item.status = ReadyItemStatus.PROCESSING
        store.save(audiobook.queue_snapshot())

        audiobook._queue._items.clear()
        audiobook.restore_queue_snapshot(store.load(audiobook.QUEUE_STATE_KEY))
        (restored,) = audiobook._queue.all_items()
        assert restored.status is ReadyItemStatus.ERROR
        assert "Interrupted" in (restored.error_message or "")
        # Nothing that Mine would pick up: the run snapshot is READY-only.
        assert not [i for i in audiobook._queue.all_items() if i.status is ReadyItemStatus.READY]

    def test_a_row_whose_files_moved_comes_back_as_a_failure(self, _home, audiobook, tmp_path):
        audio, subtitle = _pair(tmp_path, "a")
        audiobook._queue.add(audio, subtitle)
        store.save(audiobook.queue_snapshot())
        audio.unlink()

        audiobook._queue._items.clear()
        audiobook.restore_queue_snapshot(store.load(audiobook.QUEUE_STATE_KEY))
        (restored,) = audiobook._queue.all_items()
        assert restored.status is ReadyItemStatus.ERROR
        assert "a.m4b" in (restored.error_message or "")

    def test_completed_rows_keep_their_counts(self, _home, audiobook, tmp_path):
        item = audiobook._queue.add(*_pair(tmp_path, "a"))
        item.status = ReadyItemStatus.COMPLETED
        item.cards_created = 42
        store.save(audiobook.queue_snapshot())

        audiobook._queue._items.clear()
        audiobook.restore_queue_snapshot(store.load(audiobook.QUEUE_STATE_KEY))
        (restored,) = audiobook._queue.all_items()
        assert restored.status is ReadyItemStatus.COMPLETED
        assert restored.cards_created == 42

    def test_restoring_over_a_populated_queue_is_refused(self, _home, audiobook, tmp_path):
        audiobook._queue.add(*_pair(tmp_path, "a"))
        store.save(audiobook.queue_snapshot())
        assert audiobook.restore_queue_snapshot(store.load(audiobook.QUEUE_STATE_KEY)) == 0
        assert len(audiobook._queue.all_items()) == 1


class TestYouTube:
    def test_urls_and_order_survive_and_every_pending_row_is_reprobed(self, _home, youtube):
        for url in ("https://youtu.be/a", "https://youtu.be/b"):
            youtube._queue.add(url)
        store.save(youtube.queue_snapshot())

        youtube._queue._items.clear()
        probed: list[str] = []
        youtube._add_flow.retry_probe = lambda item: probed.append(item.url)
        assert youtube.restore_queue_snapshot(store.load(youtube.QUEUE_STATE_KEY)) == 2
        assert [item.url for item in youtube._queue.all_items()] == ["https://youtu.be/a", "https://youtu.be/b"]
        assert probed == ["https://youtu.be/a", "https://youtu.be/b"]

    def test_probe_output_is_never_persisted(self, _home, youtube):
        item = youtube._queue.add("https://youtu.be/a")
        item.video_info = MagicMock(title="Real Title")
        item.resolved_sub_mode = MagicMock(name="SubMode")
        item.video_id = "a"
        item.status = YouTubeItemStatus.READY
        store.save(youtube.queue_snapshot())

        raw = store.snapshot_path(youtube.QUEUE_STATE_KEY).read_text(encoding="utf-8")
        assert "resolved_sub_mode" not in raw
        assert "video_info" not in raw

        youtube._queue._items.clear()
        youtube._add_flow.retry_probe = lambda item: None
        youtube.restore_queue_snapshot(store.load(youtube.QUEUE_STATE_KEY))
        (restored,) = youtube._queue.all_items()
        assert restored.video_info is None
        assert restored.resolved_sub_mode is None
        # The label survives so the row is readable before the re-probe lands.
        assert restored.display_title == "Real Title"

    def test_an_interrupted_row_is_not_reprobed_and_not_ready(self, _home, youtube):
        item = youtube._queue.add("https://youtu.be/a")
        item.status = YouTubeItemStatus.PROCESSING
        store.save(youtube.queue_snapshot())

        youtube._queue._items.clear()
        probed: list[str] = []
        youtube._add_flow.retry_probe = lambda item: probed.append(item.url)
        youtube.restore_queue_snapshot(store.load(youtube.QUEUE_STATE_KEY))
        (restored,) = youtube._queue.all_items()
        assert restored.status is YouTubeItemStatus.ERROR
        assert "Interrupted" in (restored.error_message or "")
        assert probed == []


class TestBatch:
    def _folders(self, tmp_path, stem):
        video = tmp_path / f"{stem}-video"
        subtitle = tmp_path / f"{stem}-subs"
        video.mkdir()
        subtitle.mkdir()
        return video, subtitle

    def test_series_rows_round_trip_with_their_ids_and_offsets(self, _home, batch, tmp_path):
        ids = []
        for stem in ("one", "two"):
            video, subtitle = self._folders(tmp_path, stem)
            item = batch.batch_queue.add_item(video, subtitle, stem, 1.5)
            ids.append(item.id)
        store.save(batch.queue_snapshot())

        batch.batch_queue.clear()
        assert batch.restore_queue_snapshot(store.load(batch.QUEUE_STATE_KEY)) == 2
        restored = batch.batch_queue.get_all_items()
        assert [item.id for item in restored] == ids
        assert [item.display_name for item in restored] == ["one", "two"]
        assert [item.subtitle_offset for item in restored] == [1.5, 1.5]

    def test_an_interrupted_series_needs_an_explicit_retry_before_it_runs(self, _home, batch, tmp_path):
        video, subtitle = self._folders(tmp_path, "one")
        item = batch.batch_queue.add_item(video, subtitle, "one")
        item.status = QueueItemStatus.PROCESSING
        store.save(batch.queue_snapshot())

        batch.batch_queue.clear()
        batch.restore_queue_snapshot(store.load(batch.QUEUE_STATE_KEY))
        (restored,) = batch.batch_queue.get_all_items()
        assert restored.status is QueueItemStatus.ERROR
        assert "Interrupted" in restored.error_message
        # Nothing pending: a run started now would find no work.
        assert batch.batch_queue.get_next_pending() is None
        # Only the user's Retry turns it back into work.
        assert batch.batch_queue.reset_failed_for_retry() == 1
        assert batch.batch_queue.get_next_pending() is restored

    def test_live_anki_write_provenance_is_not_duplicated_into_the_snapshot(self, _home, batch, tmp_path):
        video, subtitle = self._folders(tmp_path, "one")
        item = batch.batch_queue.add_item(video, subtitle, "one")
        item.committed_pair_keys = {(video / "ep1.mkv", subtitle / "ep1.srt")}
        store.save(batch.queue_snapshot())
        raw = store.snapshot_path(batch.QUEUE_STATE_KEY).read_text(encoding="utf-8")
        assert "committed_pair_keys" not in raw
        assert "ep1.mkv" not in raw

    def test_a_missing_folder_comes_back_as_a_failure(self, _home, batch, tmp_path):
        video, subtitle = self._folders(tmp_path, "one")
        batch.batch_queue.add_item(video, subtitle, "one")
        store.save(batch.queue_snapshot())
        video.rmdir()

        batch.batch_queue.clear()
        batch.restore_queue_snapshot(store.load(batch.QUEUE_STATE_KEY))
        (restored,) = batch.batch_queue.get_all_items()
        assert restored.status is QueueItemStatus.ERROR
        assert "one-video" in restored.error_message


class TestReadingSubtitles:
    def test_the_file_list_round_trips_in_order(self, _home, reading_subtitles, tmp_path):
        paths = []
        for stem in ("ep01", "ep02", "ep03"):
            path = tmp_path / f"{stem}.srt"
            path.write_text("1\n")
            paths.append(path)
        reading_subtitles._add_paths(paths)
        store.save(reading_subtitles.queue_snapshot())

        reading_subtitles.file_list.clear()
        loaded = store.load(reading_subtitles.QUEUE_STATE_KEY)
        assert reading_subtitles.restore_queue_snapshot(loaded) == 3
        assert reading_subtitles.listed_paths() == paths

    def test_a_file_that_moved_is_reported_rather_than_relisted(self, _home, reading_subtitles, tmp_path):
        kept = tmp_path / "ep01.srt"
        gone = tmp_path / "ep02.srt"
        for path in (kept, gone):
            path.write_text("1\n")
        reading_subtitles._add_paths([kept, gone])
        store.save(reading_subtitles.queue_snapshot())
        gone.unlink()

        reading_subtitles.file_list.clear()
        assert reading_subtitles.restore_queue_snapshot(store.load(reading_subtitles.QUEUE_STATE_KEY)) == 1
        assert reading_subtitles.listed_paths() == [kept]


class TestPastedTextIsNeverPersisted:
    def test_the_text_tab_declares_no_queue_state_key(self):
        """D7-B: a form draft is never restored, so it is never written."""
        from anki_miner.gui.widgets.reading_text_tab import ReadingTextTab

        assert getattr(ReadingTextTab, "QUEUE_STATE_KEY", None) is None
