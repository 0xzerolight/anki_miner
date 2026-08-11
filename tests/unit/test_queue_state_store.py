"""Durable queue snapshots (D16-C) and the things they must never carry.

Two halves. ``TestRoundTrip`` proves the queue that comes back is the queue that
went in — same rows, same order, same ids. Everything else proves the store fails
closed: a hostile or stale file yields "no snapshot" rather than a half-restored
queue, a mid-run row never comes back runnable, and pasted text never reaches
disk at all.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import Qt

from anki_miner.gui.utils import queue_state_store as store
from anki_miner.gui.utils.queue_state_store import QueueItemSnapshot, QueueSnapshot
from anki_miner.gui.widgets.reading_subtitles_tab import ReadingSubtitlesTab
from anki_miner.models.mining_queue import ReadyItemStatus
from anki_miner.models.reading import ReadingSourceRef
from anki_miner.models.reading_queue import ReadingQueueItem

KEY = "queue.audiobook"


def _item(index: int, tmp_path, **kwargs) -> QueueItemSnapshot:
    audio = tmp_path / f"{index}.m4b"
    subtitle = tmp_path / f"{index}.srt"
    audio.write_bytes(b"a")
    subtitle.write_text("1")
    defaults = {
        "item_id": f"id-{index}",
        "source": store.file_pair_source(audio, subtitle),
        "title": f"Book {index}",
    }
    defaults.update(kwargs)
    return QueueItemSnapshot(**defaults)  # type: ignore[arg-type]


@pytest.fixture()
def _home(tmp_path, monkeypatch):
    """Point the store at a throwaway app home."""
    from anki_miner.gui.utils.config_manager import GUIConfigManager

    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", tmp_path / "home" / "gui_config.json")
    return tmp_path / "home"


class TestRoundTrip:
    def test_rows_come_back_in_order_with_their_ids(self, _home, tmp_path):
        items = tuple(_item(i, tmp_path) for i in range(5))
        store.save(QueueSnapshot(key=KEY, items=items))
        loaded = store.load(KEY)
        assert loaded is not None
        assert [row.item_id for row in loaded.items] == ["id-0", "id-1", "id-2", "id-3", "id-4"]
        assert [row.title for row in loaded.items] == [f"Book {i}" for i in range(5)]

    def test_every_persisted_fact_survives(self, _home, tmp_path):
        row = _item(0, tmp_path, status=store.STATUS_ERROR, retry_count=2, error="nope", result_count=17)
        store.save(QueueSnapshot(key=KEY, items=(row,)))
        loaded = store.load(KEY)
        assert loaded is not None
        (restored,) = loaded.items
        assert (restored.status, restored.retry_count, restored.error, restored.result_count) == (
            store.STATUS_ERROR,
            2,
            "nope",
            17,
        )

    def test_saving_an_empty_queue_removes_the_file(self, _home, tmp_path):
        store.save(QueueSnapshot(key=KEY, items=(_item(0, tmp_path),)))
        assert store.snapshot_path(KEY).exists()
        store.save(QueueSnapshot(key=KEY, items=()))
        assert not store.snapshot_path(KEY).exists()
        assert store.load(KEY) is None

    def test_the_write_replaces_atomically(self, _home, tmp_path, monkeypatch):
        """A half-written snapshot must never be visible under the real name."""
        store.save(QueueSnapshot(key=KEY, items=(_item(0, tmp_path),)))
        seen: list[str] = []
        real_replace = store.os.replace
        monkeypatch.setattr(
            store.os, "replace", lambda a, b: (seen.append(str(b)), real_replace(a, b))[1], raising=False
        )
        store.save(QueueSnapshot(key=KEY, items=(_item(1, tmp_path), _item(2, tmp_path))))
        assert seen == [str(store.snapshot_path(KEY))]
        loaded = store.load(KEY)
        assert loaded is not None
        assert len(loaded.items) == 2

    def test_a_failed_write_leaves_the_previous_snapshot_intact(self, _home, tmp_path, monkeypatch):
        store.save(QueueSnapshot(key=KEY, items=(_item(0, tmp_path),)))
        monkeypatch.setattr(store, "atomic_write_path", _boom)
        store.save(QueueSnapshot(key=KEY, items=(_item(1, tmp_path), _item(2, tmp_path))))
        loaded = store.load(KEY)
        assert loaded is not None
        assert [row.item_id for row in loaded.items] == ["id-0"]


def _boom(*_args, **_kwargs):
    raise OSError("disk full")


class TestHostileInput:
    def _write(self, key, payload):
        path = store.snapshot_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload) if not isinstance(payload, str) else payload, encoding="utf-8")

    def test_a_future_schema_version_is_refused(self, _home, tmp_path):
        store.save(QueueSnapshot(key=KEY, items=(_item(0, tmp_path),)))
        raw = json.loads(store.snapshot_path(KEY).read_text())
        raw["version"] = store.SCHEMA_VERSION + 1
        self._write(KEY, raw)
        assert store.load(KEY) is None

    def test_a_snapshot_filed_under_another_key_is_refused(self, _home, tmp_path):
        store.save(QueueSnapshot(key=KEY, items=(_item(0, tmp_path),)))
        raw = json.loads(store.snapshot_path(KEY).read_text())
        raw["key"] = "queue.youtube"
        self._write(KEY, raw)
        assert store.load(KEY) is None

    def test_an_oversized_file_is_refused_without_being_parsed(self, _home):
        self._write(KEY, " " * (store.MAX_BYTES + 1))
        assert store.load(KEY) is None

    def test_undecodable_json_is_refused(self, _home):
        self._write(KEY, "{not json")
        assert store.load(KEY) is None

    def test_more_rows_than_the_cap_is_refused(self, _home, tmp_path):
        rows = [_item(0, tmp_path).to_json() for _ in range(store.MAX_ITEMS + 1)]
        self._write(KEY, {"version": store.SCHEMA_VERSION, "key": KEY, "items": rows})
        assert store.load(KEY) is None

    def test_the_save_side_caps_the_row_count(self, _home, tmp_path):
        row = _item(0, tmp_path)
        store.save(QueueSnapshot(key=KEY, items=tuple([row] * (store.MAX_ITEMS + 10))))
        loaded = store.load(KEY)
        assert loaded is not None
        assert len(loaded.items) == store.MAX_ITEMS

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("id", 5),
            ("id", ""),
            ("status", "half-done"),
            ("status", None),
            ("retry_count", "2"),
            ("retry_count", -1),
            ("retry_count", True),
            ("result_count", 1.5),
            ("title", 9),
            ("error", []),
            ("source", "a path"),
            ("source", {"kind": "made_up"}),
            ("source", {"kind": "file_pair", "audio": 7, "subtitle": "x"}),
            ("source", {"kind": "url"}),
        ],
    )
    def test_a_row_that_fails_validation_is_dropped(self, _home, tmp_path, field, value):
        good = _item(0, tmp_path).to_json()
        bad = _item(1, tmp_path).to_json()
        bad[field] = value
        self._write(KEY, {"version": store.SCHEMA_VERSION, "key": KEY, "items": [good, bad]})
        loaded = store.load(KEY)
        assert loaded is not None
        assert [row.item_id for row in loaded.items] == ["id-0"]

    def test_a_row_id_cannot_escape_its_directory(self, _home):
        with pytest.raises(ValueError, match="unsafe"):
            store.snapshot_path("../../etc/passwd")
        assert store.load("../../etc/passwd") is None

    def test_a_missing_snapshot_is_simply_absent(self, _home):
        assert store.load(KEY) is None
        assert store.stored_keys() == ()


class TestInterruptedRows:
    def test_a_row_that_was_running_comes_back_interrupted(self, _home, tmp_path):
        row = _item(0, tmp_path, status=store.status_from_run_state("processing"))
        assert row.status == store.STATUS_INTERRUPTED
        store.save(QueueSnapshot(key=KEY, items=(row,)))
        loaded = store.load(KEY)
        assert loaded is not None
        assert loaded.items[0].is_interrupted is True
        assert loaded.interrupted_count == 1

    @pytest.mark.parametrize("value", ["pending", "probing", "ready", "probe_error"])
    def test_probe_states_collapse_to_ready(self, value):
        assert store.status_from_run_state(value) == store.STATUS_READY

    def test_completed_and_error_survive_as_themselves(self):
        assert store.status_from_run_state("completed") == store.STATUS_COMPLETED
        assert store.status_from_run_state("error") == store.STATUS_ERROR

    def test_a_missing_input_is_reported_so_the_row_can_fail_rather_than_run(self, tmp_path):
        audio = tmp_path / "gone.m4b"
        subtitle = tmp_path / "here.srt"
        subtitle.write_text("1")
        row = QueueItemSnapshot(item_id="x", source=store.file_pair_source(audio, subtitle))
        assert row.missing_paths() == (audio,)

    def test_a_url_row_has_no_filesystem_inputs_to_miss(self):
        row = QueueItemSnapshot(item_id="x", source=store.url_source("https://youtu.be/abc"))
        assert row.input_paths() == ()
        assert row.missing_paths() == ()

    def test_a_processing_reading_source_round_trips_as_interrupted(self, _home, tmp_path, qtbot, test_config):
        subtitle = tmp_path / "episode.srt"
        subtitle.write_text("1", encoding="utf-8")
        ref = ReadingSourceRef(kind="subtitle", path=subtitle, title="episode")
        tab = ReadingSubtitlesTab(
            config=test_config,
            processor=MagicMock(name="EpisodeProcessor"),
            presenter=MagicMock(name="Presenter"),
        )
        qtbot.addWidget(tab)
        tab._add_paths([subtitle])
        live_item = ReadingQueueItem(
            source=ref,
            title=ref.title,
            kind=ref.kind,
            status=ReadyItemStatus.PROCESSING,
            cards_created=4,
            error_message="partial",
        )
        tab.file_list.item(0).setData(Qt.ItemDataRole.UserRole, live_item)

        store.save(tab.queue_snapshot())
        loaded = store.load(tab.QUEUE_STATE_KEY)

        assert loaded is not None
        assert loaded.items[0].status == store.STATUS_INTERRUPTED
        assert loaded.items[0].error == "partial"
        assert loaded.items[0].result_count == 4


class TestReadingSources:
    def test_pasted_text_is_never_persisted(self):
        ref = ReadingSourceRef(kind="text", title="Text", text="秘密のメモ")
        assert store.reading_source(ref) is None

    def test_an_archive_backed_volume_keeps_its_ocr_member(self, tmp_path):
        archive = tmp_path / "vol1.cbz"
        archive.write_bytes(b"PK")
        ref = ReadingSourceRef(
            kind="mokuro",
            path=archive,
            image_root=archive,
            title="Series",
            volume="Vol 1",
            ocr_entry="vol1.mokuro",
        )
        source = store.reading_source(ref)
        assert source is not None
        restored = store.reading_ref_from_source(source)
        assert restored == ref

    def test_a_sidecar_mokuro_volume_round_trips(self, tmp_path):
        ref = ReadingSourceRef(
            kind="mokuro",
            path=tmp_path / "vol1.mokuro",
            image_root=tmp_path / "vol1",
            title="Series",
            volume="Vol 1",
        )
        source = store.reading_source(ref)
        assert source is not None
        assert store.reading_ref_from_source(source) == ref

    def test_a_subtitle_ref_round_trips(self, tmp_path):
        ref = ReadingSourceRef(kind="subtitle", path=tmp_path / "ep01.srt", title="ep01")
        source = store.reading_source(ref)
        assert source is not None
        assert store.reading_ref_from_source(source) == ref

    def test_a_stored_text_kind_is_still_refused_on_the_way_back_in(self):
        assert (
            store.reading_ref_from_source({"kind": store.SOURCE_READING_REF, "ref_kind": "text", "path": "x"}) is None
        )

    def test_a_non_reading_descriptor_is_not_a_reading_ref(self):
        assert store.reading_ref_from_source(store.url_source("https://x")) is None

    def test_a_reading_row_with_a_text_kind_never_survives_the_store(self, _home, tmp_path):
        raw = {
            "id": "bad",
            "source": {"kind": store.SOURCE_READING_REF, "ref_kind": "text", "path": "x", "title": "t"},
            "title": "t",
            "status": store.STATUS_READY,
            "retry_count": 0,
            "error": "",
            "result_count": 0,
        }
        path = store.snapshot_path(KEY)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": store.SCHEMA_VERSION, "key": KEY, "items": [raw]}), encoding="utf-8")
        assert store.load(KEY) is None


class TestDiscard:
    def test_discard_removes_one_snapshot(self, _home, tmp_path):
        store.save(QueueSnapshot(key=KEY, items=(_item(0, tmp_path),)))
        store.discard(KEY)
        assert store.load(KEY) is None

    def test_discard_all_clears_every_stored_queue(self, _home, tmp_path):
        for key in ("queue.audiobook", "queue.youtube", "queue.batch"):
            store.save(QueueSnapshot(key=key, items=(_item(0, tmp_path),)))
        assert len(store.stored_keys()) == 3
        store.discard_all()
        assert store.stored_keys() == ()

    def test_discard_never_reaches_outside_its_own_root(self, _home, tmp_path):
        outsider = tmp_path / "precious.json"
        outsider.write_text("keep me")
        store.discard("../precious")
        assert outsider.exists()

    def test_stored_keys_ignores_names_it_could_not_have_written(self, _home, tmp_path):
        store.save(QueueSnapshot(key=KEY, items=(_item(0, tmp_path),)))
        root = store.queue_state_root()
        (root / "notes.txt").write_text("x")
        (root / "sub").mkdir()
        assert store.stored_keys() == (KEY,)
