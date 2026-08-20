"""Tests for ResourceDownloadWorker — routing, isolation, cancel, cleanup."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.gui.workers import resource_download_worker
from anki_miner.gui.workers.resource_download_worker import (
    ResourceDownloadResult,
    ResourceDownloadSummary,
    ResourceDownloadWorker,
)
from anki_miner.services._sqlite_index import write_ownership_marker
from anki_miner.services.dictionary.importers import yomitan_importer
from anki_miner.services.frequency import source_importer as frequency_source_importer
from anki_miner.services.pitch_accent import source_importer as pitch_source_importer
from anki_miner.services.resource_catalog import ResourceSpec
from tests.unit._resume_key_assert import assert_stable_resume_key as _assert_stable_resume_key


@dataclass
class _FakeYomitanResult:
    dict_id: str = "jitendex-english"
    source_name: str = "Jitendex"
    source_revision: str = "rev"
    entry_count: int = 12345


@dataclass
class _FakeFreqResult:
    source_id: str = "jpdb"
    source_name: str = "JPDB"
    source_revision: str = "rev"
    format: str = "yomitan-freq"
    entry_count: int = 6789
    skipped_display_only: int = 0


DICT_SPEC = ResourceSpec(
    id="jitendex",
    kind="dict",
    display_name="Jitendex",
    url="https://example.test/jitendex.zip",
    license_note="note",
)
FREQ_SPEC = ResourceSpec(
    id="jpdb-freq",
    kind="freq",
    display_name="JPDB Freq",
    url="https://example.test/jpdb.zip",
    license_note="note",
)
PITCH_SPEC = ResourceSpec(
    id="kanjium-pitch",
    kind="pitch",
    display_name="Kanjium Pitch",
    url="https://example.test/accents.txt",
    license_note="note",
)

VALID_PITCH = "たべる\t食べる\t0\n".encode()


def _make_worker(specs, tmp_path: Path) -> ResourceDownloadWorker:
    return ResourceDownloadWorker(
        specs,
        dicts_root=tmp_path / "dicts",
        freqs_root=tmp_path / "freqs",
        pitch_root=tmp_path / "pitch",
        download_dir=tmp_path / "downloads",
    )


def _connect_capture(worker):
    done: list[tuple] = []
    progress: list[tuple] = []
    summaries: list[ResourceDownloadSummary] = []
    worker.item_done.connect(lambda sid, ok, detail: done.append((sid, ok, detail)))
    worker.item_progress.connect(lambda *a: progress.append(a))
    worker.finished_summary.connect(lambda s: summaries.append(s))
    return done, progress, summaries


def test_summary_pins_the_roots_captured_by_the_worker(tmp_path):
    worker = _make_worker([], tmp_path)
    _done, _progress, summaries = _connect_capture(worker)

    worker.run()

    summary = summaries[0]
    assert summary.dicts_root == tmp_path / "dicts"
    assert summary.freqs_root == tmp_path / "freqs"
    assert summary.pitch_root == tmp_path / "pitch"


def test_happy_path_all_three_kinds(tmp_path, monkeypatch):
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    download_calls: list[str] = []

    def fake_download(
        url,
        *,
        dest_dir,
        progress=None,
        cancelled_check=None,
        read_timeout_seconds=None,
        resume_key=None,
        resume_root=None,
    ):
        _assert_stable_resume_key(resume_key)
        download_calls.append(url)
        assert read_timeout_seconds == 1.0
        temp = Path(dest_dir) / f"{Path(url).name}.part"
        temp.write_bytes(VALID_PITCH if url.endswith(".txt") else b"ZIP")
        if progress is not None:
            progress(1, 1, "done")
        return temp

    dict_calls: list[dict] = []
    freq_calls: list[dict] = []

    def fake_dict(
        zip_path,
        dest_root,
        *,
        progress=None,
        overwrite=False,
        cancel_check=None,
        dict_id=None,
        before_promote=None,
    ):
        dict_calls.append({"zip_path": zip_path, "dest_root": dest_root, "overwrite": overwrite, "dict_id": dict_id})
        return _FakeYomitanResult()

    def fake_freq(
        input_path,
        dest_root,
        *,
        progress=None,
        cancel_check=None,
        overwrite=False,
        before_promote=None,
    ):
        freq_calls.append({"input_path": input_path, "dest_root": dest_root, "overwrite": overwrite})
        return _FakeFreqResult()

    monkeypatch.setattr(resource_download_worker, "download_to_temp", fake_download)
    monkeypatch.setattr(resource_download_worker, "import_yomitan_zip", fake_dict)
    monkeypatch.setattr(resource_download_worker, "import_frequency_source", fake_freq)

    worker = _make_worker([DICT_SPEC, FREQ_SPEC, PITCH_SPEC], tmp_path)
    done, _progress, summaries = _connect_capture(worker)

    worker.run()

    # finished_summary emitted exactly once.
    assert len(summaries) == 1
    summary = summaries[0]
    assert len(summary.succeeded) == 3
    assert summary.failed == []

    # dict routed with overwrite=True AND pinned to the stable catalog slot id.
    assert dict_calls[0]["overwrite"] is True
    assert dict_calls[0]["dict_id"] == "jitendex"
    dict_result = next(r for r in summary.results if r.spec_id == "jitendex")
    assert dict_result.dict_id == "jitendex-english"
    assert "12345" in dict_result.detail

    # freq routed to the configured freqs_root; result carries source_id.
    assert freq_calls[0]["dest_root"] == tmp_path / "freqs"
    assert freq_calls[0]["overwrite"] is True
    # The .part temp was re-suffixed to .zip (matches the catalog URL) before import.
    assert freq_calls[0]["input_path"].suffix == ".zip"
    freq_result = next(r for r in summary.results if r.spec_id == "jpdb-freq")
    assert freq_result.source_id == "jpdb"
    assert "6789" in freq_result.detail

    # item_done emitted per item.
    assert [d[0] for d in done] == ["jitendex", "jpdb-freq", "kanjium-pitch"]
    assert all(d[1] for d in done)


@pytest.mark.parametrize(
    ("spec", "root_name", "family", "importer_module"),
    [
        pytest.param(DICT_SPEC, "dicts", "dictionary", yomitan_importer, id="dictionary"),
        pytest.param(FREQ_SPEC, "freqs", "frequency", frequency_source_importer, id="frequency"),
        pytest.param(PITCH_SPEC, "pitch", "pitch", pitch_source_importer, id="pitch"),
    ],
)
def test_final_promotion_rechecks_resource_release_after_staging(
    tmp_path,
    monkeypatch,
    spec,
    root_name,
    family,
    importer_module,
):
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    root = tmp_path / root_name
    slot = root / spec.id
    slot.mkdir(parents=True)
    write_ownership_marker(slot, spec.id, family)
    old_marker = slot / "old-generation"
    old_marker.write_text("old", encoding="utf-8")
    with sqlite3.connect(slot / "index.sqlite") as conn:
        conn.execute("CREATE TABLE old_generation (value TEXT)")

    def fake_download(
        url,
        *,
        dest_dir,
        progress=None,
        cancelled_check=None,
        read_timeout_seconds=None,
        resume_key=None,
        resume_root=None,
    ):
        _assert_stable_resume_key(resume_key)
        temp = Path(dest_dir) / "resource.part"
        if spec.kind == "dict":
            with zipfile.ZipFile(temp, "w") as zf:
                zf.writestr(
                    "index.json",
                    json.dumps({"title": "Jitendex", "format": 3, "revision": "rev"}),
                )
                zf.writestr(
                    "term_bank_1.json",
                    json.dumps([["猫", "ねこ", "", "", 0, ["cat"], 1, ""]]),
                )
        elif spec.kind == "freq":
            with zipfile.ZipFile(temp, "w") as zf:
                zf.writestr(
                    "index.json",
                    json.dumps({"title": "JPDB Freq", "format": 3, "revision": "rev"}),
                )
                zf.writestr("term_meta_bank_1.json", json.dumps([["猫", "freq", 5]]))
        else:
            temp.write_bytes(VALID_PITCH)
        return temp

    monkeypatch.setattr(resource_download_worker, "download_to_temp", fake_download)
    monkeypatch.setattr(resource_download_worker, "sweep_superseded_dicts", lambda *_a, **_kw: ([], []))

    worker = _make_worker([spec], tmp_path)
    release_checks: list[tuple[bool, bool, bool]] = []
    reopened: list[sqlite3.Connection] = []
    promotion_seam_active = False
    promotion_calls: list[tuple[Path, bool]] = []

    real_promote_staged_dir = importer_module.promote_staged_dir

    def reopen_at_promotion(staging, final, *, mover, overwrite, before_promote=None):
        nonlocal promotion_seam_active
        promotion_calls.append((final, (staging / "index.sqlite").is_file()))
        reopened.append(sqlite3.connect(slot / "index.sqlite"))
        promotion_seam_active = True
        try:
            return real_promote_staged_dir(
                staging,
                final,
                mover=mover,
                overwrite=overwrite,
                before_promote=before_promote,
            )
        finally:
            promotion_seam_active = False

    def release_resources(request) -> None:
        allowed = not reopened
        release_checks.append((promotion_seam_active, bool(reopened), allowed))
        request.resolve(allowed)

    monkeypatch.setattr(importer_module, "promote_staged_dir", reopen_at_promotion)
    worker.require_promotion_approval()
    worker.promotion_requested.connect(release_resources)
    _done, _progress, summaries = _connect_capture(worker)

    try:
        worker.run()
    finally:
        for connection in reopened:
            connection.close()

    assert promotion_calls == [(slot, True)]
    assert release_checks == [(False, False, True), (True, True, False)]
    assert summaries[0].succeeded == []
    assert len(summaries[0].failed) == 1
    assert "Indexed resources became busy" in summaries[0].failed[0].detail
    assert old_marker.read_text(encoding="utf-8") == "old"


def test_per_item_failure_isolation(tmp_path, monkeypatch):
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()

    def fake_download(
        url,
        *,
        dest_dir,
        progress=None,
        cancelled_check=None,
        read_timeout_seconds=None,
        resume_key=None,
        resume_root=None,
    ):
        _assert_stable_resume_key(resume_key)
        temp = Path(dest_dir) / f"{Path(url).name}.part"
        temp.write_bytes(VALID_PITCH if url.endswith(".txt") else b"DATA")
        return temp

    def fake_dict(
        zip_path,
        dest_root,
        *,
        progress=None,
        overwrite=False,
        cancel_check=None,
        dict_id=None,
        before_promote=None,
    ):
        return _FakeYomitanResult()

    def fake_freq(
        input_path,
        dest_root,
        *,
        progress=None,
        cancel_check=None,
        overwrite=False,
        before_promote=None,
    ):
        raise RuntimeError("freq boom")

    monkeypatch.setattr(resource_download_worker, "download_to_temp", fake_download)
    monkeypatch.setattr(resource_download_worker, "import_yomitan_zip", fake_dict)
    monkeypatch.setattr(resource_download_worker, "import_frequency_source", fake_freq)

    worker = _make_worker([DICT_SPEC, FREQ_SPEC, PITCH_SPEC], tmp_path)
    done, _progress, summaries = _connect_capture(worker)

    worker.run()

    summary = summaries[0]
    assert len(summary.succeeded) == 2
    assert len(summary.failed) == 1
    freq_result = summary.failed[0]
    assert freq_result.spec_id == "jpdb-freq"
    assert freq_result.ok is False
    assert "freq boom" in freq_result.detail

    # dict + pitch still succeeded.
    assert {r.spec_id for r in summary.succeeded} == {"jitendex", "kanjium-pitch"}
    assert ("jpdb-freq", False, freq_result.detail) in done


def test_valid_pitch_download_imports_into_pitch_root(tmp_path, monkeypatch):
    """The pitch route is chain-native: a real import lands at
    pitch_root/<spec.id>/index.sqlite and the result carries source_id so
    apply_download_summary can prepend the chain entry (a missing source_id
    would leave that branch dead code and pitch inactive until restart)."""
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    pitch_root = tmp_path / "anki_miner_home" / "pitch"

    def fake_download(
        url,
        *,
        dest_dir,
        progress=None,
        cancelled_check=None,
        read_timeout_seconds=None,
        resume_key=None,
        resume_root=None,
    ):
        _assert_stable_resume_key(resume_key)
        temp = Path(dest_dir) / "accents.txt.part"
        temp.write_bytes(VALID_PITCH)
        return temp

    monkeypatch.setattr(resource_download_worker, "download_to_temp", fake_download)

    worker = ResourceDownloadWorker(
        [PITCH_SPEC],
        dicts_root=tmp_path / "dicts",
        freqs_root=tmp_path / "freqs",
        pitch_root=pitch_root,
        download_dir=download_dir,
    )
    _done, _progress, summaries = _connect_capture(worker)

    worker.run()

    result = summaries[0].succeeded[0]
    assert result.source_id == "kanjium-pitch"
    slot = pitch_root / "kanjium-pitch"
    assert (slot / "index.sqlite").is_file()
    # The .part temp was re-suffixed to .txt (matching the catalog URL) so the
    # importer's CSV path handled it; the source copy keeps that suffix.
    assert (slot / "source.txt").is_file()
    assert "1" in result.detail


def test_pitch_redownload_overwrites_pinned_slot(tmp_path, monkeypatch):
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    pitch_root = tmp_path / "anki_miner_home" / "pitch"

    def fake_download(
        url,
        *,
        dest_dir,
        progress=None,
        cancelled_check=None,
        read_timeout_seconds=None,
        resume_key=None,
        resume_root=None,
    ):
        _assert_stable_resume_key(resume_key)
        temp = Path(dest_dir) / "accents.txt.part"
        temp.write_bytes(VALID_PITCH)
        return temp

    monkeypatch.setattr(resource_download_worker, "download_to_temp", fake_download)

    for _ in range(2):  # second run must overwrite in place, not fail/fork
        worker = ResourceDownloadWorker(
            [PITCH_SPEC],
            dicts_root=tmp_path / "dicts",
            freqs_root=tmp_path / "freqs",
            pitch_root=pitch_root,
            download_dir=download_dir,
        )
        _done, _progress, summaries = _connect_capture(worker)
        worker.run()
        assert summaries[0].succeeded[0].source_id == "kanjium-pitch"

    assert (pitch_root / "kanjium-pitch" / "index.sqlite").is_file()


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"\xff\xfe", id="malformed-utf8"),
        pytest.param(b"not enough columns\n", id="zero-valid-rows"),
    ],
)
def test_invalid_pitch_download_preserves_existing_slot(tmp_path, monkeypatch, payload):
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    pitch_root = tmp_path / "anki_miner_home" / "pitch"

    # Seed a valid slot from a prior good download.
    def good_download(
        url,
        *,
        dest_dir,
        progress=None,
        cancelled_check=None,
        read_timeout_seconds=None,
        resume_key=None,
        resume_root=None,
    ):
        _assert_stable_resume_key(resume_key)
        temp = Path(dest_dir) / "accents.txt.part"
        temp.write_bytes(VALID_PITCH)
        return temp

    monkeypatch.setattr(resource_download_worker, "download_to_temp", good_download)
    seed = ResourceDownloadWorker(
        [PITCH_SPEC],
        dicts_root=tmp_path / "dicts",
        freqs_root=tmp_path / "freqs",
        pitch_root=pitch_root,
        download_dir=download_dir,
    )
    _connect_capture(seed)
    seed.run()
    index = pitch_root / "kanjium-pitch" / "index.sqlite"
    old_hash = hashlib.sha256(index.read_bytes()).hexdigest()

    def bad_download(
        url,
        *,
        dest_dir,
        progress=None,
        cancelled_check=None,
        read_timeout_seconds=None,
        resume_key=None,
        resume_root=None,
    ):
        _assert_stable_resume_key(resume_key)
        temp = Path(dest_dir) / "accents.txt.part"
        temp.write_bytes(payload)
        return temp

    monkeypatch.setattr(resource_download_worker, "download_to_temp", bad_download)
    worker = ResourceDownloadWorker(
        [PITCH_SPEC],
        dicts_root=tmp_path / "dicts",
        freqs_root=tmp_path / "freqs",
        pitch_root=pitch_root,
        download_dir=download_dir,
    )
    _done, _progress, summaries = _connect_capture(worker)

    worker.run()

    assert summaries[0].succeeded == []
    assert summaries[0].failed[0].spec_id == "kanjium-pitch"
    # Existing slot untouched (staging-dir import never mutates the canonical
    # slot until atomic promotion).
    assert hashlib.sha256(index.read_bytes()).hexdigest() == old_hash


def test_cancellation_stops_loop_early(tmp_path, monkeypatch):
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    download_calls: list[str] = []

    def fake_download(
        url,
        *,
        dest_dir,
        progress=None,
        cancelled_check=None,
        read_timeout_seconds=None,
        resume_key=None,
        resume_root=None,
    ):
        _assert_stable_resume_key(resume_key)
        download_calls.append(url)
        temp = Path(dest_dir) / f"{Path(url).name}.part"
        temp.write_bytes(b"DATA")
        return temp

    def fake_dict(
        zip_path,
        dest_root,
        *,
        progress=None,
        overwrite=False,
        cancel_check=None,
        dict_id=None,
        before_promote=None,
    ):
        return _FakeYomitanResult()

    monkeypatch.setattr(resource_download_worker, "download_to_temp", fake_download)
    monkeypatch.setattr(resource_download_worker, "import_yomitan_zip", fake_dict)

    worker = _make_worker([DICT_SPEC, FREQ_SPEC, PITCH_SPEC], tmp_path)
    _done, _progress, summaries = _connect_capture(worker)

    worker.cancel()  # flag set before run
    worker.run()

    # Loop stopped before any item ran; no crash; summary still emitted.
    assert download_calls == []
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.cancelled is True
    assert summary.requested_count == 3
    assert summary.completed_count == 0
    assert summary.not_processed_count == 3
    assert summary.results == []
    assert summary.failed == []


def test_cancellation_after_completed_item_keeps_success_and_marks_rest_not_processed(tmp_path, monkeypatch):
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    worker = _make_worker([DICT_SPEC, FREQ_SPEC, PITCH_SPEC], tmp_path)
    download_calls: list[str] = []

    def fake_download(
        url,
        *,
        dest_dir,
        progress=None,
        cancelled_check=None,
        read_timeout_seconds=None,
        resume_key=None,
        resume_root=None,
    ):
        _assert_stable_resume_key(resume_key)
        download_calls.append(url)
        temp = Path(dest_dir) / f"{Path(url).name}.part"
        temp.write_bytes(b"ZIP")
        return temp

    def fake_dict(
        zip_path,
        dest_root,
        *,
        progress=None,
        overwrite=False,
        cancel_check=None,
        dict_id=None,
        before_promote=None,
    ):
        worker.cancel()
        return _FakeYomitanResult()

    monkeypatch.setattr(resource_download_worker, "download_to_temp", fake_download)
    monkeypatch.setattr(resource_download_worker, "import_yomitan_zip", fake_dict)
    _done, _progress, summaries = _connect_capture(worker)

    worker.run()

    summary = summaries[0]
    assert download_calls == [DICT_SPEC.url]
    assert summary.cancelled is True
    assert [result.spec_id for result in summary.succeeded] == [DICT_SPEC.id]
    assert summary.failed == []
    assert summary.completed_count == 1
    assert summary.not_processed_count == 2


def test_cancellation_exception_is_not_recorded_as_failure(tmp_path, monkeypatch):
    worker = _make_worker([DICT_SPEC, FREQ_SPEC], tmp_path)

    def cancel_during_download(
        url,
        *,
        dest_dir,
        progress=None,
        cancelled_check=None,
        read_timeout_seconds=None,
        resume_key=None,
        resume_root=None,
    ):
        _assert_stable_resume_key(resume_key)
        worker.cancel()
        raise SetupError("Failed to download: read timed out")

    monkeypatch.setattr(resource_download_worker, "download_to_temp", cancel_during_download)
    done, _progress, summaries = _connect_capture(worker)

    worker.run()

    summary = summaries[0]
    assert summary.cancelled is True
    assert summary.failed == []
    assert summary.completed_count == 0
    assert summary.not_processed_count == 2
    assert done == []


def test_leftover_temp_cleanup_when_importer_fails(tmp_path, monkeypatch):
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    created_temps: list[Path] = []

    def fake_download(
        url,
        *,
        dest_dir,
        progress=None,
        cancelled_check=None,
        read_timeout_seconds=None,
        resume_key=None,
        resume_root=None,
    ):
        _assert_stable_resume_key(resume_key)
        temp = Path(dest_dir) / f"{Path(url).name}.part"
        temp.write_bytes(b"DATA")
        created_temps.append(temp)
        return temp

    def fake_dict(
        zip_path,
        dest_root,
        *,
        progress=None,
        overwrite=False,
        cancel_check=None,
        dict_id=None,
        before_promote=None,
    ):
        raise RuntimeError("import boom")

    monkeypatch.setattr(resource_download_worker, "download_to_temp", fake_download)
    monkeypatch.setattr(resource_download_worker, "import_yomitan_zip", fake_dict)

    worker = _make_worker([DICT_SPEC], tmp_path)
    _done, _progress, summaries = _connect_capture(worker)

    worker.run()

    assert summaries[0].failed[0].spec_id == "jitendex"
    # The downloaded temp must be cleaned up since the importer consumed nothing.
    assert created_temps and not created_temps[0].exists()


def test_freq_route_imports_real_source_into_freqs_root(tmp_path, monkeypatch):
    # Exercise the REAL frequency importer (not monkeypatched) to prove the freq
    # route builds freqs/<source_id>/index.sqlite and threads source_id back.
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()

    def fake_download(
        url,
        *,
        dest_dir,
        progress=None,
        cancelled_check=None,
        read_timeout_seconds=None,
        resume_key=None,
        resume_root=None,
    ):
        _assert_stable_resume_key(resume_key)
        # download_to_temp always stages a ``.part`` file; the worker re-suffixes
        # it to .zip from the catalog URL before handing it to the importer.
        temp = Path(dest_dir) / "freq-download.part"
        index = {"title": "JPDB v2.2", "format": 3, "revision": "rev1"}
        with zipfile.ZipFile(temp, "w") as zf:
            zf.writestr("index.json", json.dumps(index))
            zf.writestr("term_meta_bank_1.json", json.dumps([["猫", "freq", 5]]))
        return temp

    monkeypatch.setattr(resource_download_worker, "download_to_temp", fake_download)

    worker = _make_worker([FREQ_SPEC], tmp_path)
    _done, _progress, summaries = _connect_capture(worker)
    worker.run()

    summary = summaries[0]
    assert len(summary.succeeded) == 1
    result = summary.succeeded[0]
    assert result.source_id == "jpdb-v2-2"  # slug of the zip title
    assert "1 entries" in result.detail

    db = tmp_path / "freqs" / "jpdb-v2-2" / "index.sqlite"
    assert db.exists()
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT term, rank FROM entries").fetchall()
    finally:
        conn.close()
    assert rows == [("猫", 5)]


def test_summary_properties_filter_results():
    summary = ResourceDownloadSummary(
        results=[
            ResourceDownloadResult("a", "dict", "A", "u", ok=True, detail="ok"),
            ResourceDownloadResult("b", "freq", "B", "u", ok=False, detail="bad"),
        ],
        requested_count=3,
    )
    assert [r.spec_id for r in summary.succeeded] == ["a"]
    assert [r.spec_id for r in summary.failed] == ["b"]
    assert summary.completed_count == 2
    assert summary.not_processed_count == 1


def _seed_dict_dir(dicts_root: Path, dict_id: str, source_name: str) -> None:
    """Create dicts_root/<dict_id>/index.sqlite with a source_name meta row."""
    from anki_miner.services.dictionary.storage import SCHEMA_VERSION, create_index, write_meta

    db = dicts_root / dict_id / "index.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    create_index(db)
    write_meta(
        db,
        {
            "schema_version": str(SCHEMA_VERSION),
            "source_name": source_name,
        },
    )


def _run_dict_download(tmp_path, monkeypatch, *, imported_source_name: str):
    """Drive the worker for a single dict spec with a real sweep, fake importer."""
    download_dir = tmp_path / "downloads"
    download_dir.mkdir(exist_ok=True)

    def fake_download(
        url,
        *,
        dest_dir,
        progress=None,
        cancelled_check=None,
        read_timeout_seconds=None,
        resume_key=None,
        resume_root=None,
    ):
        _assert_stable_resume_key(resume_key)
        temp = Path(dest_dir) / f"{Path(url).name}.part"
        temp.write_bytes(b"ZIP")
        return temp

    def fake_dict(
        zip_path,
        dest_root,
        *,
        progress=None,
        overwrite=False,
        cancel_check=None,
        dict_id=None,
        before_promote=None,
    ):
        # Land the pinned slot on disk so the sweep sees a keep_id dir.
        _seed_dict_dir(Path(dest_root), dict_id, imported_source_name)
        return _FakeYomitanResult(dict_id=dict_id, source_name=imported_source_name)

    monkeypatch.setattr(resource_download_worker, "download_to_temp", fake_download)
    monkeypatch.setattr(resource_download_worker, "import_yomitan_zip", fake_dict)

    worker = _make_worker([DICT_SPEC], tmp_path)
    _done, _progress, summaries = _connect_capture(worker)
    worker.run()
    return summaries[0], tmp_path / "dicts"


def test_dict_download_sweeps_legacy_date_versioned_copy(tmp_path, monkeypatch):
    dicts_root = tmp_path / "dicts"
    # A pre-fix date-versioned Jitendex already installed under its old id.
    _seed_dict_dir(dicts_root, "jitendex-org-2025-11-05", "Jitendex.org [2025-11-05]")

    summary, dicts_root = _run_dict_download(tmp_path, monkeypatch, imported_source_name="Jitendex.org [2026-06-06]")

    result = next(r for r in summary.results if r.spec_id == "jitendex")
    assert result.ok is True
    assert result.removed_dicts == [("jitendex-org-2025-11-05", "Jitendex.org [2025-11-05]")]
    assert result.failed_removals == []
    # Legacy dir gone; pinned slot remains.
    assert not (dicts_root / "jitendex-org-2025-11-05").exists()
    assert (dicts_root / "jitendex" / "index.sqlite").exists()


def test_dict_download_sweep_noop_when_imported_title_has_no_date(tmp_path, monkeypatch):
    dicts_root = tmp_path / "dicts"
    # An unrelated dict whose base would collide but the NEW title has no bracket.
    _seed_dict_dir(dicts_root, "jitendex-org-2025-11-05", "Jitendex.org [2025-11-05]")

    summary, dicts_root = _run_dict_download(tmp_path, monkeypatch, imported_source_name="Jitendex")

    result = next(r for r in summary.results if r.spec_id == "jitendex")
    assert result.removed_dicts == []
    # Nothing swept because the imported dict itself is not date-bracketed.
    assert (dicts_root / "jitendex-org-2025-11-05").exists()


def test_dict_download_sweep_survives_corrupt_sibling(tmp_path, monkeypatch):
    dicts_root = tmp_path / "dicts"
    _seed_dict_dir(dicts_root, "jitendex-org-2025-11-05", "Jitendex.org [2025-11-05]")
    # A corrupt/foreign sibling index.sqlite (no meta table) must not abort the sweep.
    corrupt = dicts_root / "broken-dict" / "index.sqlite"
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_bytes(b"not a database")

    summary, dicts_root = _run_dict_download(tmp_path, monkeypatch, imported_source_name="Jitendex.org [2026-06-06]")

    result = next(r for r in summary.results if r.spec_id == "jitendex")
    assert result.ok is True  # import not failed by the bad sibling
    assert result.removed_dicts == [("jitendex-org-2025-11-05", "Jitendex.org [2025-11-05]")]
    assert not (dicts_root / "jitendex-org-2025-11-05").exists()
    assert (dicts_root / "broken-dict").exists()  # left untouched


def test_sweep_not_invoked_on_freq_or_pitch(tmp_path, monkeypatch):
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()

    def fake_download(
        url,
        *,
        dest_dir,
        progress=None,
        cancelled_check=None,
        read_timeout_seconds=None,
        resume_key=None,
        resume_root=None,
    ):
        _assert_stable_resume_key(resume_key)
        temp = Path(dest_dir) / f"{Path(url).name}.part"
        temp.write_bytes(VALID_PITCH if url.endswith(".txt") else b"ZIP")
        return temp

    def fake_freq(
        input_path,
        dest_root,
        *,
        progress=None,
        cancel_check=None,
        source_id=None,
        overwrite=False,
        before_promote=None,
    ):
        return _FakeFreqResult()

    sweep_calls: list[tuple] = []

    def spy_sweep(dicts_root, *, keep_id, imported_source_name):
        sweep_calls.append((keep_id, imported_source_name))
        return [], []

    monkeypatch.setattr(resource_download_worker, "download_to_temp", fake_download)
    monkeypatch.setattr(resource_download_worker, "import_frequency_source", fake_freq)
    monkeypatch.setattr(resource_download_worker, "sweep_superseded_dicts", spy_sweep)

    worker = _make_worker([FREQ_SPEC, PITCH_SPEC], tmp_path)
    _done, _progress, summaries = _connect_capture(worker)
    worker.run()

    assert len(summaries[0].succeeded) == 2
    assert sweep_calls == []  # sweep only fires on the dict route


# ---------------------------------------------------------------------------
# Typed phase progress: the worker states which phase it is in, so the view
# never has to infer one by matching English progress messages.
# ---------------------------------------------------------------------------


def _phase_events(worker) -> list:
    events: list = []
    worker.item_progress.connect(events.append)
    return events


def test_download_progress_carries_real_bytes_and_the_downloading_phase(tmp_path, monkeypatch):
    def fake_download(
        url,
        *,
        dest_dir,
        progress=None,
        cancelled_check=None,
        read_timeout_seconds=None,
        resume_key=None,
        resume_root=None,
    ):
        _assert_stable_resume_key(resume_key)
        temp = Path(dest_dir) / "d.part"
        temp.parent.mkdir(parents=True, exist_ok=True)
        temp.write_bytes(b"ZIP")
        progress(0, 600, "Downloading")
        progress(155, 600, "Downloading")
        return temp

    monkeypatch.setattr(resource_download_worker, "download_to_temp", fake_download)
    monkeypatch.setattr(resource_download_worker, "import_yomitan_zip", lambda *a, **kw: _FakeYomitanResult())
    monkeypatch.setattr(resource_download_worker, "sweep_superseded_dicts", lambda *a, **kw: ([], []))

    worker = _make_worker([DICT_SPEC], tmp_path)
    events = _phase_events(worker)
    worker.run()

    downloads = [e for e in events if e.phase is resource_download_worker.ResourcePhase.DOWNLOADING]
    assert [(e.downloaded, e.total_bytes) for e in downloads] == [(0, 600), (155, 600)]
    assert {e.display_name for e in downloads} == {"Jitendex"}
    assert {e.spec_id for e in downloads} == {"jitendex"}


def test_absent_content_length_reports_no_total_rather_than_zero(tmp_path, monkeypatch):
    def fake_download(
        url,
        *,
        dest_dir,
        progress=None,
        cancelled_check=None,
        read_timeout_seconds=None,
        resume_key=None,
        resume_root=None,
    ):
        _assert_stable_resume_key(resume_key)
        temp = Path(dest_dir) / "d.part"
        temp.parent.mkdir(parents=True, exist_ok=True)
        temp.write_bytes(b"ZIP")
        progress(155, 0, "Downloading")
        return temp

    monkeypatch.setattr(resource_download_worker, "download_to_temp", fake_download)
    monkeypatch.setattr(resource_download_worker, "import_yomitan_zip", lambda *a, **kw: _FakeYomitanResult())
    monkeypatch.setattr(resource_download_worker, "sweep_superseded_dicts", lambda *a, **kw: ([], []))

    worker = _make_worker([DICT_SPEC], tmp_path)
    events = _phase_events(worker)
    worker.run()

    first = next(e for e in events if e.phase is resource_download_worker.ResourcePhase.DOWNLOADING)
    assert first.total_bytes is None


def test_install_phase_opens_before_the_importer_and_keeps_the_transferred_size(tmp_path, monkeypatch):
    order: list[str] = []

    def fake_download(
        url,
        *,
        dest_dir,
        progress=None,
        cancelled_check=None,
        read_timeout_seconds=None,
        resume_key=None,
        resume_root=None,
    ):
        _assert_stable_resume_key(resume_key)
        temp = Path(dest_dir) / "d.part"
        temp.parent.mkdir(parents=True, exist_ok=True)
        temp.write_bytes(b"ZIP")
        progress(629_145_600, 629_145_600, "Downloading")
        return temp

    def fake_dict(
        zip_path,
        dest_root,
        *,
        progress=None,
        overwrite=False,
        cancel_check=None,
        dict_id=None,
        before_promote=None,
    ):
        order.append("import")
        return _FakeYomitanResult()

    monkeypatch.setattr(resource_download_worker, "download_to_temp", fake_download)
    monkeypatch.setattr(resource_download_worker, "import_yomitan_zip", fake_dict)
    monkeypatch.setattr(resource_download_worker, "sweep_superseded_dicts", lambda *a, **kw: ([], []))

    worker = _make_worker([DICT_SPEC], tmp_path)
    events: list = []
    worker.item_progress.connect(lambda e: (events.append(e), order.append(e.phase.value)))
    worker.run()

    installing = [e for e in events if e.phase is resource_download_worker.ResourcePhase.INSTALLING]
    assert installing, "the download→install transition must be announced"
    assert order.index("installing") < order.index("import")
    assert installing[0].downloaded == 629_145_600
    assert installing[0].entries is None


def test_entry_counts_promote_to_indexing_and_never_fall_back(tmp_path, monkeypatch):
    def fake_download(
        url,
        *,
        dest_dir,
        progress=None,
        cancelled_check=None,
        read_timeout_seconds=None,
        resume_key=None,
        resume_root=None,
    ):
        _assert_stable_resume_key(resume_key)
        temp = Path(dest_dir) / "d.part"
        temp.parent.mkdir(parents=True, exist_ok=True)
        temp.write_bytes(b"ZIP")
        return temp

    def fake_dict(
        zip_path,
        dest_root,
        *,
        progress=None,
        overwrite=False,
        cancel_check=None,
        dict_id=None,
        before_promote=None,
    ):
        # cur/total during insert are a scaled files_done/bank fraction, not
        # entries — cur=4 here is deliberately a plausible-looking bank-count
        # reading that must NOT be latched as an entry count; only the
        # message's real inserted figure may be.
        progress(0, 0, "Validating archive")
        progress(4, 4, "Inserted 184,200 entries")
        progress(1000, 1000, "Finalizing import")  # A message with no count is not a regression.
        return _FakeYomitanResult()

    monkeypatch.setattr(resource_download_worker, "download_to_temp", fake_download)
    monkeypatch.setattr(resource_download_worker, "import_yomitan_zip", fake_dict)
    monkeypatch.setattr(resource_download_worker, "sweep_superseded_dicts", lambda *a, **kw: ([], []))

    worker = _make_worker([DICT_SPEC], tmp_path)
    events = _phase_events(worker)
    worker.run()

    phases = [e.phase for e in events]
    assert resource_download_worker.ResourcePhase.INDEXING in phases
    indexed = [e for e in events if e.phase is resource_download_worker.ResourcePhase.INDEXING]
    # ``current`` (4) is a bank-fraction reading and never becomes the entry
    # count; the reporter parses the true figure out of the "Inserted N
    # entries" message text instead, so both INDEXING events read 184,200 —
    # the "Finalizing import" call carries no count but the reading latches.
    assert [e.entries for e in indexed] == [184200, 184200]
    # Once a count is landing, nothing walks the phase back to installing.
    assert phases.index(resource_download_worker.ResourcePhase.INDEXING) == len(phases) - 2


def test_file_counting_importers_never_report_a_fabricated_entry_count(tmp_path, monkeypatch):
    def fake_download(
        url,
        *,
        dest_dir,
        progress=None,
        cancelled_check=None,
        read_timeout_seconds=None,
        resume_key=None,
        resume_root=None,
    ):
        _assert_stable_resume_key(resume_key)
        temp = Path(dest_dir) / "f.part"
        temp.parent.mkdir(parents=True, exist_ok=True)
        temp.write_bytes(b"ZIP")
        return temp

    def fake_freq(
        input_path,
        dest_root,
        *,
        progress=None,
        cancel_check=None,
        overwrite=False,
        before_promote=None,
    ):
        progress(1, 4, "term_meta_bank_1.json")  # A file index, NOT entries.
        return _FakeFreqResult()

    monkeypatch.setattr(resource_download_worker, "download_to_temp", fake_download)
    monkeypatch.setattr(resource_download_worker, "import_frequency_source", fake_freq)

    worker = _make_worker([FREQ_SPEC], tmp_path)
    events = _phase_events(worker)
    worker.run()

    assert all(e.entries is None for e in events)
    assert all(e.phase is not resource_download_worker.ResourcePhase.INDEXING for e in events)


def test_each_resource_starts_its_own_phase_sequence(tmp_path, monkeypatch):
    def fake_download(
        url,
        *,
        dest_dir,
        progress=None,
        cancelled_check=None,
        read_timeout_seconds=None,
        resume_key=None,
        resume_root=None,
    ):
        _assert_stable_resume_key(resume_key)
        temp = Path(dest_dir) / f"{Path(url).name}.part"
        temp.parent.mkdir(parents=True, exist_ok=True)
        temp.write_bytes(VALID_PITCH if url.endswith(".txt") else b"ZIP")
        progress(10, 10, "Downloading")
        return temp

    def fake_dict(
        zip_path,
        dest_root,
        *,
        progress=None,
        overwrite=False,
        cancel_check=None,
        dict_id=None,
        before_promote=None,
    ):
        progress(99, 0, "Inserted 99 entries")
        return _FakeYomitanResult()

    def fake_freq(
        input_path,
        dest_root,
        *,
        progress=None,
        cancel_check=None,
        overwrite=False,
        before_promote=None,
    ):
        return _FakeFreqResult()

    monkeypatch.setattr(resource_download_worker, "download_to_temp", fake_download)
    monkeypatch.setattr(resource_download_worker, "import_yomitan_zip", fake_dict)
    monkeypatch.setattr(resource_download_worker, "import_frequency_source", fake_freq)
    monkeypatch.setattr(resource_download_worker, "sweep_superseded_dicts", lambda *a, **kw: ([], []))

    worker = _make_worker([DICT_SPEC, FREQ_SPEC], tmp_path)
    events = _phase_events(worker)
    worker.run()

    freq_events = [e for e in events if e.spec_id == "jpdb-freq"]
    assert freq_events[0].phase is resource_download_worker.ResourcePhase.DOWNLOADING
    assert all(e.entries is None for e in freq_events)
