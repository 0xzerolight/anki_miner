"""Terminal receipts for the workers that had none.

Every worker start line needs a matching end line: a start with no end is the
diagnosis (the thread never returned), and a run's duration cannot be read off
two timestamps when a log holds several interleaved runs. These tests pin the
end lines, plus the two whole-run failures that used to leave nothing behind —
a fatal tool error killing a 40-file Condense queue, and a recommended resource
that failed to download or import.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.exceptions import YouTubeFetchError
from anki_miner.gui.workers import prewarm_worker as prewarm_worker_module
from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.gui.workers.file_queue_worker import FileQueueWorker
from anki_miner.gui.workers.import_worker import ImportWorker
from anki_miner.gui.workers.manual_pair_worker import ManualPairWorkerThread
from anki_miner.gui.workers.prewarm_worker import PrewarmWorker
from anki_miner.gui.workers.youtube_playlist_probe_worker import (
    YouTubePlaylistProbeWorker,
    YouTubePlaylistResolveWorker,
)
from anki_miner.gui.workers.youtube_probe_worker import YouTubeProbeWorker
from anki_miner.models.processing import ProcessingResult

_FILE_QUEUE_LOGGER = "anki_miner.gui.workers.file_queue_worker"
_RESOURCE_LOGGER = "anki_miner.gui.workers.resource_download_worker"
_PREWARM_LOGGER = "anki_miner.gui.workers.prewarm_worker"
_PROBE_LOGGER = "anki_miner.gui.workers.youtube_probe_worker"
_PLAYLIST_LOGGER = "anki_miner.gui.workers.youtube_playlist_probe_worker"
_IMPORT_LOGGER = "anki_miner.gui.workers.import_worker"
_MANUAL_LOGGER = "anki_miner.gui.workers.manual_pair_worker"


def _lines(caplog, prefix: str) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.getMessage().startswith(prefix)]


def _one(caplog, prefix: str) -> str:
    found = _lines(caplog, prefix)
    assert len(found) == 1, f"expected exactly one {prefix!r} line, got {found} in {caplog.text}"
    return found[0]


# ---------------------------------------------------------------------------
# FileQueueWorker — end line and the fatal tool-error abort
# ---------------------------------------------------------------------------


class _FatalToolError(RuntimeError):
    pass


class _ScriptedFileQueueWorker(FileQueueWorker):
    """Runs a script of ``ok`` / ``skip`` / ``fail`` / ``fatal`` items."""

    _FATAL_QUEUE_EXCEPTIONS = (_FatalToolError,)

    def __init__(self, script):
        super().__init__()
        self._script = script

    def _queue_items(self):
        return self._script

    def _process_item(self, idx, item):
        if item == "fatal":
            raise _FatalToolError("ffmpeg has no libopus encoder")
        if item == "skip":
            self.file_skipped.emit(idx, f"/tmp/out{idx}", "Skipped, exists")
        elif item == "fail":
            self.file_finished.emit(idx, None, "boom")
        else:
            self.file_finished.emit(idx, item, None)


def test_file_queue_worker_end_line_carries_elapsed_and_counts(qapp, caplog):
    """The queue closes its start receipt with the run's duration and tallies."""
    worker = _ScriptedFileQueueWorker(["ok", "skip", "fail"])

    with caplog.at_level(logging.INFO, logger=__name__):
        worker.run()

    end = _one(caplog, "_ScriptedFileQueueWorker finished:")
    assert "elapsed_s=" in end
    assert "succeeded=1" in end
    assert "skipped=1" in end
    assert "failed=1" in end


def test_file_queue_worker_records_the_fatal_abort(qapp, caplog):
    """A missing encoder that kills the whole queue leaves a WARNING behind."""
    worker = _ScriptedFileQueueWorker(["ok", "fatal", "ok", "ok"])

    with caplog.at_level(logging.WARNING, logger=_FILE_QUEUE_LOGGER):
        worker.run()

    aborted = _one(caplog, "Queue aborted by fatal tool error:")
    assert "worker=_ScriptedFileQueueWorker" in aborted
    assert "idx=1" in aborted
    assert "error_type=_FatalToolError" in aborted
    assert "libopus" in aborted
    record = next(r for r in caplog.records if r.getMessage().startswith("Queue aborted by fatal tool error:"))
    assert record.levelno == logging.WARNING


# ---------------------------------------------------------------------------
# ResourceDownloadWorker — per-resource failure record and end line
# ---------------------------------------------------------------------------


def _resource_worker(tmp_path: Path, specs):
    from anki_miner.gui.workers.resource_download_worker import ResourceDownloadWorker

    return ResourceDownloadWorker(
        specs,
        dicts_root=tmp_path / "dicts",
        freqs_root=tmp_path / "freqs",
        pitch_root=tmp_path / "pitch",
        download_dir=tmp_path / "downloads",
    )


def _dict_spec():
    from anki_miner.services.resource_catalog import ResourceSpec

    return ResourceSpec(
        id="jitendex",
        kind="dict",
        display_name="Jitendex",
        url="https://example.test/jitendex.zip",
        license_note="note",
    )


def test_resource_download_worker_records_a_failed_resource(tmp_path, qapp, caplog, monkeypatch):
    """A resource that fails to download names itself, its kind and its URL."""
    from anki_miner.gui.workers import resource_download_worker as module

    def boom(*_args, **_kwargs):
        raise OSError("connection reset by peer")

    monkeypatch.setattr(module, "download_to_temp", boom)
    worker = _resource_worker(tmp_path, [_dict_spec()])

    with caplog.at_level(logging.INFO, logger=_RESOURCE_LOGGER):
        worker.run()

    failed = _one(caplog, "Resource failed:")
    assert "id=jitendex" in failed
    assert "kind=dict" in failed
    assert "https://example.test/jitendex.zip" in failed
    assert "error_type=OSError" in failed
    assert "connection reset by peer" in failed
    record = next(r for r in caplog.records if r.getMessage().startswith("Resource failed:"))
    assert record.levelno == logging.WARNING

    end = _one(caplog, "ResourceDownloadWorker finished:")
    assert "elapsed_s=" in end
    assert "succeeded=0" in end
    assert "failed=1" in end
    assert "cancelled=False" in end


# ---------------------------------------------------------------------------
# PrewarmWorker — start shape, end line, failure at WARNING
# ---------------------------------------------------------------------------


def test_prewarm_worker_is_a_cancellable_worker():
    """It joins the worker family so the start/end ratchet covers it."""
    assert issubclass(PrewarmWorker, CancellableWorker)


def test_prewarm_worker_start_names_the_chain_it_warms(test_config, qapp, caplog, monkeypatch):
    """The start receipt says which language and chain the warm is paying for."""
    import anki_miner.services.tagger as tagger_module

    monkeypatch.setattr(tagger_module, "get_shared_tagger", MagicMock())
    monkeypatch.setattr(prewarm_worker_module, "build_definition_service", MagicMock(return_value=MagicMock()))

    worker = PrewarmWorker(test_config)
    with caplog.at_level(logging.INFO, logger=_PREWARM_LOGGER):
        worker.run()

    start = _one(caplog, "PrewarmWorker started:")
    assert f"language={test_config.language}" in start
    assert "chain=" in start
    assert "dicts_root=" in start
    assert "elapsed_s=" in _one(caplog, "PrewarmWorker finished:")


def test_prewarm_worker_failure_is_a_warning(test_config, qapp, caplog, monkeypatch):
    """A failed warm predicts a slow first mine, so it is not a DEBUG whisper."""
    monkeypatch.setattr(
        prewarm_worker_module,
        "build_definition_service",
        MagicMock(side_effect=RuntimeError("simulated sqlite open failure")),
    )

    worker = PrewarmWorker(test_config)
    with caplog.at_level(logging.INFO, logger=_PREWARM_LOGGER):
        worker.run()

    failed = _one(caplog, "Prewarm failed:")
    assert "error_type=RuntimeError" in failed
    assert "simulated sqlite open failure" in failed
    record = next(r for r in caplog.records if r.getMessage().startswith("Prewarm failed:"))
    assert record.levelno == logging.WARNING
    # Best-effort still means best-effort: the failure never escapes run().
    assert _lines(caplog, "PrewarmWorker finished:")


# ---------------------------------------------------------------------------
# YouTube probes — own class name on failure, URL + duration on success
# ---------------------------------------------------------------------------


def test_playlist_resolve_failure_uses_its_own_class_name(qapp, caplog):
    """A playlist resolve failure no longer reports as YouTubeProbeWorker."""
    fetcher = MagicMock()
    fetcher.probe_playlist.side_effect = YouTubeFetchError("playlist is private")
    worker = YouTubePlaylistResolveWorker(fetcher, "https://youtube.com/playlist?list=PL1", 5)

    with caplog.at_level(logging.INFO, logger=_PLAYLIST_LOGGER):
        worker.run()

    assert _lines(caplog, "YouTubePlaylistResolveWorker: playlist is private")
    assert not _lines(caplog, "YouTubeProbeWorker: ")


def test_probe_done_line_carries_the_url_and_the_duration(qapp, caplog):
    """The probe's end line says which URL it spent that time on."""
    fetcher = MagicMock()
    fetcher.probe_metadata.return_value = SimpleNamespace(video_id="abc")
    worker = YouTubeProbeWorker(fetcher, "https://www.youtube.com/watch?v=abc123")

    with caplog.at_level(logging.INFO, logger=_PROBE_LOGGER):
        worker.run()

    end = _one(caplog, "YouTubeProbeWorker finished:")
    assert "elapsed_s=" in end
    assert "v=abc123" in end


def test_playlist_probe_worker_end_line_counts_entries(qapp, caplog):
    """Sequential entry probing closes with how many entries survived."""
    fetcher = MagicMock()
    fetcher.probe_metadata.side_effect = [
        SimpleNamespace(video_id="a"),
        YouTubeFetchError("entry unavailable"),
    ]
    worker = YouTubePlaylistProbeWorker(fetcher, ["https://youtu.be/a", "https://youtu.be/b"])

    with caplog.at_level(logging.INFO, logger=_PLAYLIST_LOGGER):
        worker.run()

    end = _one(caplog, "YouTubePlaylistProbeWorker finished:")
    assert "elapsed_s=" in end
    assert "urls=2" in end
    assert "probed=1" in end
    assert "failed=1" in end


# ---------------------------------------------------------------------------
# ImportWorker / ManualPairWorkerThread end lines
# ---------------------------------------------------------------------------


def test_import_worker_end_line_reports_the_outcome(qapp, caplog):
    """Importer runs close their receipt whether they land or fail."""
    worker = ImportWorker(lambda _progress, _cancel: ("jitendex-english", {"entry_count": 3}))

    with caplog.at_level(logging.INFO, logger=_IMPORT_LOGGER):
        worker.run()

    end = _one(caplog, "ImportWorker finished:")
    assert "elapsed_s=" in end
    assert "ok=True" in end
    assert "resource_id=jitendex-english" in end


def test_manual_pair_worker_end_line_reports_pair_tallies(tmp_path, qapp, caplog):
    """The quick-pairs run closes with what it actually mined."""
    processor = MagicMock()
    processor.config = SimpleNamespace(subtitle_offset=0.0)
    processor.process_episode.return_value = ProcessingResult(
        total_words_found=4,
        new_words_found=2,
        cards_created=2,
    )
    processor._preflight_card_target = MagicMock()
    processor.check_offline_dictionary = MagicMock()
    pair = SimpleNamespace(video=tmp_path / "ep1.mkv", subtitle=tmp_path / "ep1.ass")
    worker = ManualPairWorkerThread(processor, [pair])

    with caplog.at_level(logging.INFO, logger=_MANUAL_LOGGER):
        worker.run()

    end = _one(caplog, "ManualPairWorkerThread finished:")
    assert "elapsed_s=" in end
    assert "results=1" in end
    assert "succeeded=1" in end
    assert "cards=2" in end
