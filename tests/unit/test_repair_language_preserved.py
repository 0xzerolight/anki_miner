"""A repair must not rewrite a non-ja index's language back to "ja"."""

from __future__ import annotations

import contextlib
import sqlite3
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QMessageBox

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils import file_dialogs
from anki_miner.gui.widgets.settings_tab import SettingsTab
from anki_miner.services._sqlite_index import meta_language, read_slot_language, slot_language_kwarg
from anki_miner.services.audio_packs.importer import import_audio_pack, repair_audio_pack
from anki_miner.services.audio_packs.storage import read_meta_cached as pack_meta
from anki_miner.services.dictionary.importers.yomitan_importer import (
    import_yomitan_zip,
    repair_yomitan_zip,
)
from anki_miner.services.dictionary.storage import read_meta as dict_meta
from anki_miner.services.frequency import storage as freq_storage
from anki_miner.services.frequency.source_importer import (
    import_frequency_source,
    repair_frequency_source,
)
from anki_miner.services.pitch_accent import storage as pitch_storage
from anki_miner.services.pitch_accent.source_importer import (
    import_pitch_source,
    repair_pitch_source,
)
from tests.fixtures.dictionary.build_yomitan_fixture import build_yomitan_zip
from tests.unit.test_audio_pack_registry import _make_ajt_pack


def test_read_slot_language_defaults_to_ja_on_a_broken_slot(tmp_path: Path):
    slot = tmp_path / "broken"
    slot.mkdir()
    assert read_slot_language(slot) == "ja"
    (slot / "index.sqlite").write_bytes(b"not a database")
    assert read_slot_language(slot) == "ja"


def test_read_slot_language_falls_back_to_sqlite_without_a_sidecar(tmp_path: Path):
    csv_path = tmp_path / "f.csv"
    csv_path.write_text("term,rank\n猫,5\n", encoding="utf-8")
    dest = tmp_path / "freqs"
    import_frequency_source(csv_path, dest, source_id="zh-freq", language="zh")
    (dest / "zh-freq" / "meta.json").unlink()
    assert read_slot_language(dest / "zh-freq") == "zh"


def test_dictionary_repair_keeps_zh(tmp_path: Path):
    zip_path = build_yomitan_zip(tmp_path / "src" / "d.zip")
    dest = tmp_path / "dicts"
    import_yomitan_zip(zip_path, dest, dict_id="zh-dict", language="zh")
    repair_yomitan_zip(zip_path, dest, dict_id="zh-dict")
    assert meta_language(dict_meta(dest / "zh-dict" / "index.sqlite")) == "zh"


def test_frequency_repair_keeps_zh(tmp_path: Path):
    csv_path = tmp_path / "f.csv"
    csv_path.write_text("term,rank\n猫,5\n", encoding="utf-8")
    dest = tmp_path / "freqs"
    import_frequency_source(csv_path, dest, source_id="zh-freq", language="zh")
    repair_frequency_source(csv_path, dest, source_id="zh-freq", source_name="SUBTLEX")
    assert meta_language(freq_storage.read_meta_cached(dest / "zh-freq" / "index.sqlite")) == "zh"


def test_pitch_repair_keeps_ja_and_ko(tmp_path: Path):
    csv_path = tmp_path / "p.csv"
    csv_path.write_text("ねこ,猫,1\n", encoding="utf-8")
    dest = tmp_path / "pitch"
    import_pitch_source(csv_path, dest, source_id="ja-pitch")
    import_pitch_source(csv_path, dest, source_id="ko-pitch", language="ko")
    repair_pitch_source(csv_path, dest, source_id="ja-pitch", source_name="Kanjium")
    repair_pitch_source(csv_path, dest, source_id="ko-pitch", source_name="KO")
    assert meta_language(pitch_storage.read_meta_cached(dest / "ja-pitch" / "index.sqlite")) == "ja"
    assert meta_language(pitch_storage.read_meta_cached(dest / "ko-pitch" / "index.sqlite")) == "ko"


def test_audio_pack_repair_keeps_ko_even_when_the_index_is_corrupt(tmp_path: Path):
    pack_dir = _make_ajt_pack(tmp_path / "pack")
    dest = tmp_path / "packs"
    import_audio_pack(pack_dir, dest, pack_id="ko-pack", language="ko")
    # Corrupt the index but leave the sidecar: this is the branch that quarantines
    # the slot, and the language must still survive the rebuild.
    with sqlite3.connect(dest / "ko-pack" / "index.sqlite") as conn:
        conn.execute("DROP TABLE entries")
    repair_audio_pack(pack_dir, dest, pack_id="ko-pack")
    assert meta_language(pack_meta(dest / "ko-pack" / "index.sqlite")) == "ko"


# ---------------------------------------------------------------------------
# Reimport All rebuilds a slot through the ordinary IMPORT path, which stamps
# its own default. Three call sites therefore have to replay the slot's stamp:
# dictionary Reimport All, audio-pack Reimport All (android_db), and the
# single android_db re-point.
# ---------------------------------------------------------------------------


def test_slot_language_kwarg_carries_only_a_non_ja_stamp(tmp_path: Path):
    csv_path = tmp_path / "f.csv"
    csv_path.write_text("term,rank\n猫,5\n", encoding="utf-8")
    dest = tmp_path / "freqs"
    import_frequency_source(csv_path, dest, source_id="ja-freq")
    import_frequency_source(csv_path, dest, source_id="zh-freq", language="zh")

    # A ja slot contributes no keyword at all, so every pre-transition call
    # site stays byte-identical.
    assert slot_language_kwarg(dest / "ja-freq") == {}
    assert slot_language_kwarg(dest / "missing") == {}
    assert slot_language_kwarg(dest / "zh-freq") == {"language": "zh"}


def _stub_import_worker() -> MagicMock:
    """A MagicMock shaped like an ImportWorker: signals connect, nothing runs."""
    worker = MagicMock(name="ImportWorker")
    for signal in ("progress", "import_finished", "failed", "cancelled", "finished"):
        setattr(worker, signal, MagicMock())
    worker.is_cancelled = False
    worker.isRunning = MagicMock(return_value=True)
    return worker


def _wind_down(worker: MagicMock) -> None:
    """Emit the domain result then the native-finished barrier, closing the dialog."""
    worker.import_finished.connect.call_args.args[0]("ignored", {"entry_count": 0})
    worker.isRunning.return_value = False
    for call in tuple(worker.finished.connect.call_args_list):
        call.args[0]()


@pytest.fixture
def language_flow_tab(test_config: AnkiMinerConfig, tmp_path: Path, qtbot, monkeypatch):
    """A SettingsTab whose dict and audio-pack roots live under tmp_path."""
    cfg = replace(
        test_config,
        dicts_root=tmp_path / "dicts",
        audio_packs_root=tmp_path / "packs",
    )
    (tmp_path / "dicts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "packs").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **kw: 0)
    widget = SettingsTab(cfg)
    qtbot.addWidget(widget)
    yield widget
    widget.shutdown()
    for worker in widget.iter_close_workers():
        if worker is not None:
            worker.wait(3000)
    qtbot.wait(10)
    with contextlib.suppress(RuntimeError):
        widget.deleteLater()


def _install_android_db_slot(packs_root: Path, pack_id: str, source_db: Path, language: str) -> None:
    """Write an android_db pack slot stamped *language*, pointing at *source_db*."""
    from anki_miner.services.audio_packs.storage import SCHEMA_VERSION, create_index, write_meta

    slot = packs_root / pack_id
    slot.mkdir(parents=True, exist_ok=True)
    db = slot / "index.sqlite"
    create_index(db)
    write_meta(
        db,
        {
            "pack_id": pack_id,
            "source": pack_id,
            "format": "android_db",
            "entry_count": "1",
            "schema_version": str(SCHEMA_VERSION),
            "source_db": str(source_db),
            "language": language,
        },
    )


def test_dictionary_reimport_all_replays_the_slot_stamp(language_flow_tab, monkeypatch, tmp_path: Path):
    tab = language_flow_tab
    zip_path = build_yomitan_zip(tmp_path / "src" / "d.zip")
    import_yomitan_zip(zip_path, tab.config.dicts_root, dict_id="zh-dict", language="zh")
    import_yomitan_zip(zip_path, tab.config.dicts_root, dict_id="ja-dict")
    worker = _stub_import_worker()
    factory = MagicMock(name="for_yomitan", return_value=worker)
    monkeypatch.setattr(
        "anki_miner.gui.controllers.dictionary_import_flow.ImportWorker.for_yomitan",
        factory,
    )

    tab._dict_import_flow.reimport_all(_scan_result=([("yomitan", "zh-dict", "ZH", zip_path)], []))
    assert factory.call_args.kwargs.get("language") == "zh"
    _wind_down(worker)

    factory.reset_mock()
    worker = _stub_import_worker()
    factory.return_value = worker
    tab._dict_import_flow.reimport_all(_scan_result=([("yomitan", "ja-dict", "JA", zip_path)], []))
    assert "language" not in factory.call_args.kwargs
    _wind_down(worker)


def test_audio_pack_reimport_all_replays_the_slot_stamp(language_flow_tab, monkeypatch, tmp_path: Path):
    tab = language_flow_tab
    source_db = tmp_path / "android.db"
    source_db.touch()
    _install_android_db_slot(tab.config.audio_packs_root, "ko-pack", source_db, "ko")
    worker = _stub_import_worker()
    factory = MagicMock(name="for_android_audio_db", return_value=worker)
    monkeypatch.setattr(
        "anki_miner.gui.controllers.audio_pack_import_flow.ImportWorker.for_android_audio_db",
        factory,
    )

    tab._audio_pack_import_flow.reimport_all(
        _scan_result=([("android_db", "ko-pack", "KO", source_db)], [], True),
    )

    assert factory.call_args.kwargs.get("language") == "ko"
    _wind_down(worker)


def test_single_android_db_repoint_replays_the_slot_stamp(language_flow_tab, monkeypatch, tmp_path: Path):
    tab = language_flow_tab
    source_db = tmp_path / "android.db"
    source_db.touch()
    _install_android_db_slot(tab.config.audio_packs_root, "ko-pack", source_db, "ko")
    monkeypatch.setattr(
        file_dialogs,
        "pick_open_file",
        lambda *a, on_done, **kw: on_done(str(source_db)),
    )
    factory = MagicMock(name="for_android_audio_db", return_value=MagicMock())
    monkeypatch.setattr(
        "anki_miner.gui.controllers.audio_pack_import_flow.ImportWorker.for_android_audio_db",
        factory,
    )
    monkeypatch.setattr(tab._audio_pack_import_flow, "_run_modal_import", MagicMock())

    tab._audio_pack_import_flow.reimport_pack("ko-pack")

    assert factory.call_args.kwargs.get("language") == "ko"
