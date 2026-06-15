"""Tests for AudioPackImportWorker."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.gui.workers.audio_pack_import_worker import AudioPackImportWorker

# ---------------------------------------------------------------------------
# Pack-building helpers (inline — mirrors test_audio_pack_importer.py)
# ---------------------------------------------------------------------------


def _make_forvo_pack(directory: Path, n_entries: int = 2) -> Path:
    """Create a minimal Forvo-format audio pack under *directory*."""
    speakers = ["alice", "bob"]
    words = ["食べる", "飲む", "走る", "見る"]
    for i in range(n_entries):
        speaker = speakers[i % len(speakers)]
        word = words[i % len(words)]
        speaker_dir = directory / speaker
        speaker_dir.mkdir(parents=True, exist_ok=True)
        (speaker_dir / f"{word}.mp3").touch()
    return directory


def _make_ajt_pack(directory: Path, n_entries: int = 2) -> Path:
    """Create a minimal AJT-format audio pack under *directory*."""
    media_dir = directory / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    headwords: dict = {}
    files_meta: dict = {}
    words = ["食べる", "飲む", "走る", "見る", "来る"]
    for i in range(n_entries):
        word = words[i % len(words)]
        fname = f"word_{i}.mp3"
        (media_dir / fname).touch()
        headwords.setdefault(word, []).append(fname)
        files_meta[fname] = {"kana_reading": f"reading_{i}", "pitch_number": str(i)}
    (directory / "index.json").write_text(
        json.dumps({"headwords": headwords, "files": files_meta}),
        encoding="utf-8",
    )
    return directory


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


def test_import_emits_finished(tmp_path: Path, qapp):
    pack = _make_forvo_pack(tmp_path / "forvo_pack")
    dest = tmp_path / "dicts"
    worker = AudioPackImportWorker.for_pack(pack, dest)

    finished_pack_ids: list[str] = []
    failed_errors: list[str] = []
    worker.import_finished.connect(lambda pack_id, meta: finished_pack_ids.append(pack_id))
    worker.failed.connect(lambda err: failed_errors.append(err))

    worker.run()

    assert not failed_errors
    assert len(finished_pack_ids) == 1
    assert finished_pack_ids[0] == "forvo-pack"


def test_import_finished_meta_contains_expected_keys(tmp_path: Path, qapp):
    pack = _make_forvo_pack(tmp_path / "forvo_pack")
    dest = tmp_path / "dicts"
    worker = AudioPackImportWorker.for_pack(pack, dest)

    metas: list[dict] = []
    worker.import_finished.connect(lambda pack_id, meta: metas.append(meta))

    worker.run()

    assert metas
    meta = metas[0]
    assert "entry_count" in meta
    assert "source_name" in meta
    assert "format" in meta
    assert meta["entry_count"] >= 1
    assert meta["source_name"] == "forvo-pack"
    assert meta["format"] == "forvo"


def test_import_finished_progress_strings_observed(tmp_path: Path, qapp):
    pack = _make_ajt_pack(tmp_path / "ajt_pack")
    dest = tmp_path / "dicts"
    worker = AudioPackImportWorker.for_pack(pack, dest)

    progress_messages: list[str] = []
    worker.progress.connect(lambda msg: progress_messages.append(msg))

    worker.run()

    assert progress_messages, "expected at least one progress message"
    for msg in progress_messages:
        assert isinstance(msg, str)
        assert msg.strip()


def test_import_correct_args_pack_id_override(tmp_path: Path, qapp):
    pack = _make_ajt_pack(tmp_path / "ajt_pack")
    dest = tmp_path / "dicts"
    worker = AudioPackImportWorker.for_pack(pack, dest, pack_id="custom-id")

    finished_pack_ids: list[str] = []
    worker.import_finished.connect(lambda pack_id, meta: finished_pack_ids.append(pack_id))

    worker.run()

    assert finished_pack_ids == ["custom-id"]
    assert (dest / "custom-id" / "index.sqlite").exists()


def test_import_correct_args_dest_root(tmp_path: Path, qapp):
    pack = _make_forvo_pack(tmp_path / "forvo_pack")
    dest = tmp_path / "my_audio_dest"
    worker = AudioPackImportWorker.for_pack(pack, dest)

    finished: list[str] = []
    worker.import_finished.connect(lambda pack_id, meta: finished.append(pack_id))

    worker.run()

    assert finished
    assert (dest / finished[0] / "index.sqlite").exists()


def test_import_overwrite_passthrough(tmp_path: Path, qapp):
    """overwrite=True should be forwarded to the importer."""
    pack = _make_ajt_pack(tmp_path / "ajt_pack")
    dest = tmp_path / "dicts"

    # First import
    worker1 = AudioPackImportWorker.for_pack(pack, dest)
    worker1.run()

    # Second import without overwrite — should fail
    failed: list[str] = []
    worker2 = AudioPackImportWorker.for_pack(pack, dest, overwrite=False)
    worker2.failed.connect(lambda err: failed.append(err))
    worker2.run()
    assert failed, "expected failure without overwrite"

    # Third import with overwrite — should succeed
    finished: list[str] = []
    worker3 = AudioPackImportWorker.for_pack(pack, dest, overwrite=True)
    worker3.import_finished.connect(lambda pack_id, meta: finished.append(pack_id))
    worker3.run()
    assert finished, "expected success with overwrite=True"


# ---------------------------------------------------------------------------
# SetupError → failed
# ---------------------------------------------------------------------------


def test_setup_error_emits_failed(tmp_path: Path, qapp):
    bad = tmp_path / "not_a_pack"
    bad.mkdir()
    (bad / "random.txt").write_text("hello")
    worker = AudioPackImportWorker.for_pack(bad, tmp_path / "dicts")

    failed_errors: list[str] = []
    finished: list[str] = []
    worker.failed.connect(lambda err: failed_errors.append(err))
    worker.import_finished.connect(lambda pack_id, meta: finished.append(pack_id))

    worker.run()

    assert failed_errors
    assert not finished


def test_setup_error_no_import_finished(tmp_path: Path, qapp):
    """SetupError (already-exists) must not emit import_finished."""
    pack = _make_ajt_pack(tmp_path / "pack")
    dest = tmp_path / "dists"

    worker1 = AudioPackImportWorker.for_pack(pack, dest)
    worker1.run()

    finished: list[str] = []
    failed: list[str] = []
    worker2 = AudioPackImportWorker.for_pack(pack, dest, overwrite=False)
    worker2.import_finished.connect(lambda pack_id, meta: finished.append(pack_id))
    worker2.failed.connect(lambda err: failed.append(err))
    worker2.run()

    assert not finished
    assert failed


# ---------------------------------------------------------------------------
# Unexpected exception → failed
# ---------------------------------------------------------------------------


def test_unexpected_exception_emits_failed(tmp_path: Path, qapp):
    pack = _make_ajt_pack(tmp_path / "pack")
    dest = tmp_path / "dists"

    with patch(
        "anki_miner.gui.workers.audio_pack_import_worker.import_audio_pack",
        side_effect=RuntimeError("unexpected boom"),
    ):
        worker = AudioPackImportWorker.for_pack(pack, dest)

        failed_errors: list[str] = []
        finished: list[str] = []
        worker.failed.connect(lambda err: failed_errors.append(err))
        worker.import_finished.connect(lambda pack_id, meta: finished.append(pack_id))

        worker.run()

    assert failed_errors
    assert "unexpected boom" in failed_errors[0]
    assert not finished


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_cancel_aborts_import(tmp_path: Path, qapp):
    pack = _make_ajt_pack(tmp_path / "pack")
    dest = tmp_path / "dicts"

    worker = AudioPackImportWorker.for_pack(pack, dest)

    failed_errors: list[str] = []
    finished_pack_ids: list[str] = []
    worker.failed.connect(lambda err: failed_errors.append(err))
    worker.import_finished.connect(lambda pack_id, meta: finished_pack_ids.append(pack_id))

    # Pre-cancel before running so the importer aborts on the very first cancel_check
    worker.cancel()
    worker.run()

    assert finished_pack_ids == []
    assert any("cancel" in err.lower() for err in failed_errors)
    # Dest folder must not have a partial import
    assert not dest.exists() or not any(dest.iterdir())


def test_cancel_before_run_no_import_finished(tmp_path: Path, qapp):
    pack = _make_forvo_pack(tmp_path / "forvo_pack")
    dest = tmp_path / "dicts"

    worker = AudioPackImportWorker.for_pack(pack, dest)
    worker.cancel()

    finished: list[str] = []
    worker.import_finished.connect(lambda pack_id, meta: finished.append(pack_id))
    worker.run()

    assert finished == []
