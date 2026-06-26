"""Tests for ResourceDownloadWorker — routing, isolation, cancel, cleanup."""

from __future__ import annotations

import json
import sqlite3
import zipfile
from dataclasses import dataclass
from pathlib import Path

from anki_miner.gui.workers import resource_download_worker
from anki_miner.gui.workers.resource_download_worker import (
    ResourceDownloadResult,
    ResourceDownloadSummary,
    ResourceDownloadWorker,
)
from anki_miner.services.resource_catalog import ResourceSpec


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


def _make_worker(specs, tmp_path: Path) -> ResourceDownloadWorker:
    return ResourceDownloadWorker(
        specs,
        dicts_root=tmp_path / "dicts",
        freqs_root=tmp_path / "freqs",
        pitch_csv=tmp_path / "pitch_accent.csv",
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


def test_happy_path_all_three_kinds(tmp_path, monkeypatch):
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    download_calls: list[str] = []

    def fake_download(url, *, dest_dir, progress=None, cancelled_check=None):
        download_calls.append(url)
        temp = Path(dest_dir) / f"{Path(url).name}.part"
        temp.write_bytes(b"PITCHBYTES" if url.endswith(".txt") else b"ZIP")
        if progress is not None:
            progress(1, 1, "done")
        return temp

    dict_calls: list[dict] = []
    freq_calls: list[dict] = []

    def fake_dict(zip_path, dest_root, *, progress=None, overwrite=False, cancel_check=None):
        dict_calls.append({"zip_path": zip_path, "dest_root": dest_root, "overwrite": overwrite})
        return _FakeYomitanResult()

    def fake_freq(input_path, dest_root, *, progress=None, cancel_check=None):
        freq_calls.append({"input_path": input_path, "dest_root": dest_root})
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

    # dict routed with overwrite=True; result carries dict_id.
    assert dict_calls[0]["overwrite"] is True
    dict_result = next(r for r in summary.results if r.spec_id == "jitendex")
    assert dict_result.dict_id == "jitendex-english"
    assert "12345" in dict_result.detail

    # freq routed to the configured freqs_root; result carries source_id.
    assert freq_calls[0]["dest_root"] == tmp_path / "freqs"
    # The .part temp was re-suffixed to .zip (matches the catalog URL) before import.
    assert freq_calls[0]["input_path"].suffix == ".zip"
    freq_result = next(r for r in summary.results if r.spec_id == "jpdb-freq")
    assert freq_result.source_id == "jpdb"
    assert "6789" in freq_result.detail

    # item_done emitted per item.
    assert [d[0] for d in done] == ["jitendex", "jpdb-freq", "kanjium-pitch"]
    assert all(d[1] for d in done)


def test_per_item_failure_isolation(tmp_path, monkeypatch):
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()

    def fake_download(url, *, dest_dir, progress=None, cancelled_check=None):
        temp = Path(dest_dir) / f"{Path(url).name}.part"
        temp.write_bytes(b"DATA")
        return temp

    def fake_dict(zip_path, dest_root, *, progress=None, overwrite=False, cancel_check=None):
        return _FakeYomitanResult()

    def fake_freq(input_path, dest_root, *, progress=None, cancel_check=None):
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


def test_pitch_routing_moves_temp_to_dest(tmp_path, monkeypatch):
    # download_dir and pitch_csv live under separate subdirs to mirror the real
    # cross-filesystem layout (temp dir vs ~/.anki_miner). The route uses
    # shutil.move (not os.replace), which handles cross-device moves; this
    # exercises its normal in-tree path and documents that intent.
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    pitch_csv = tmp_path / "anki_miner_home" / "pitch_accent.csv"
    expected = b"PITCH ACCENT TSV CONTENT"

    def fake_download(url, *, dest_dir, progress=None, cancelled_check=None):
        temp = Path(dest_dir) / "accents.txt.part"
        temp.write_bytes(expected)
        return temp

    monkeypatch.setattr(resource_download_worker, "download_to_temp", fake_download)

    worker = ResourceDownloadWorker(
        [PITCH_SPEC],
        dicts_root=tmp_path / "dicts",
        freqs_root=tmp_path / "freqs",
        pitch_csv=pitch_csv,
        download_dir=download_dir,
    )
    _done, _progress, summaries = _connect_capture(worker)

    worker.run()

    assert pitch_csv.exists()  # parent dir created + file moved into place
    assert pitch_csv.read_bytes() == expected
    assert summaries[0].succeeded[0].detail == "downloaded"


def test_cancellation_stops_loop_early(tmp_path, monkeypatch):
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    download_calls: list[str] = []

    def fake_download(url, *, dest_dir, progress=None, cancelled_check=None):
        download_calls.append(url)
        temp = Path(dest_dir) / f"{Path(url).name}.part"
        temp.write_bytes(b"DATA")
        return temp

    def fake_dict(zip_path, dest_root, *, progress=None, overwrite=False, cancel_check=None):
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
    assert summaries[0].results == []


def test_leftover_temp_cleanup_when_importer_fails(tmp_path, monkeypatch):
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    created_temps: list[Path] = []

    def fake_download(url, *, dest_dir, progress=None, cancelled_check=None):
        temp = Path(dest_dir) / f"{Path(url).name}.part"
        temp.write_bytes(b"DATA")
        created_temps.append(temp)
        return temp

    def fake_dict(zip_path, dest_root, *, progress=None, overwrite=False, cancel_check=None):
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

    def fake_download(url, *, dest_dir, progress=None, cancelled_check=None):
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
        ]
    )
    assert [r.spec_id for r in summary.succeeded] == ["a"]
    assert [r.spec_id for r in summary.failed] == ["b"]
