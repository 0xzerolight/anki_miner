"""Module-level seams the command line shares with the GUI queue workers."""

from __future__ import annotations

import stat
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from anki_miner.exceptions.youtube import BotDetectionError, YouTubeFetchError
from anki_miner.gui.workers import reading_queue_worker, youtube_queue_worker
from anki_miner.gui.workers._queue_worker_base import anki_write_state_of, exception_retry_eligible
from anki_miner.models import AnkiWriteState


def test_classify_probe_result_moved_and_widget_alias_is_same_function() -> None:
    from anki_miner.gui.widgets import youtube_playlist_flow
    from anki_miner.services.youtube_fetcher import classify_probe_result

    assert youtube_playlist_flow._classify_probe_result is classify_probe_result


def test_generic_fetch_error_is_retry_eligible() -> None:
    assert exception_retry_eligible(YouTubeFetchError("connection reset"), AnkiWriteState.NOTE_WRITE_UNCERTAIN)


def test_deterministic_fetch_error_is_not_retry_eligible() -> None:
    assert not exception_retry_eligible(BotDetectionError("sign in"), AnkiWriteState.NO_NOTE_WRITE)


def test_unknown_exception_is_not_retry_eligible() -> None:
    assert not exception_retry_eligible(RuntimeError("x"), AnkiWriteState.NO_NOTE_WRITE)


def test_anki_write_state_of_fails_closed() -> None:
    assert anki_write_state_of(object()) is AnkiWriteState.NOTE_WRITE_UNCERTAIN
    proc = SimpleNamespace(anki_service=SimpleNamespace(anki_write_state=AnkiWriteState.NO_NOTE_WRITE))
    assert anki_write_state_of(proc) is AnkiWriteState.NO_NOTE_WRITE


def test_allocate_youtube_workspace_is_private_dir_under_media_temp(test_config, tmp_path: Path) -> None:
    config = replace(test_config, media_temp_folder=tmp_path / "media")
    workspace = youtube_queue_worker.allocate_youtube_workspace(config)
    assert workspace.parent == tmp_path / "media" / "youtube"
    assert workspace.name.startswith("run-")
    if sys.platform != "win32":
        assert stat.S_IMODE(workspace.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(workspace.stat().st_mode) == 0o700


def test_load_reading_source_passes_parser_seams_and_cancel_check(test_config) -> None:
    parser = SimpleNamespace(normalize=lambda s: s, has_target_script=lambda s: True)
    processor = SimpleNamespace(subtitle_parser=parser)
    ref = MagicMock()
    cancel_check = MagicMock(return_value=False)
    with patch.object(reading_queue_worker.detector, "load", return_value="DOC") as load:
        doc = reading_queue_worker.load_reading_source(processor, test_config, ref, cancel_check=cancel_check)
    assert doc == "DOC"
    args, kwargs = load.call_args
    assert args == (ref,)
    assert kwargs["cancel_check"] is cancel_check
    assert kwargs["normalize"] is parser.normalize
    assert kwargs["has_target_script"] is parser.has_target_script
    assert "encodings" in kwargs and "rules" in kwargs


def test_load_reading_source_omits_absent_parser_seams(test_config) -> None:
    processor = SimpleNamespace(subtitle_parser=None)
    with patch.object(reading_queue_worker.detector, "load", return_value="DOC") as load:
        reading_queue_worker.load_reading_source(processor, test_config, MagicMock(), cancel_check=lambda: False)
    kwargs = load.call_args.kwargs
    assert "normalize" not in kwargs
    assert "has_target_script" not in kwargs
