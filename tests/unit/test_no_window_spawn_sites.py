"""Regression guard: every external-binary spawn spreads ``no_window_kwargs``.

Platform-independent — instead of mutating ``sys.platform`` globally, each test
patches the module's ``no_window_kwargs`` to a sentinel and asserts the patched
``subprocess.run``/``Popen`` received it. Proves the call site wires the helper
through; the helper's own win32/off-Windows behaviour is covered in
``test_subprocess_utils``.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.services.media_extractor import MediaExtractorService
from anki_miner.services.validation_service import ValidationService
from anki_miner.utils.audio_track_detector import _run_ffprobe_json

SENTINEL = {"creationflags": 0x424242}

ME = "anki_miner.services.media_extractor"
ATD = "anki_miner.utils.audio_track_detector"
VS = "anki_miner.services.validation_service"


@pytest.fixture
def extractor(test_config):
    with patch(f"{ME}.ensure_directory"):
        return MediaExtractorService(test_config)


def _finished_popen():
    proc = MagicMock()
    proc.returncode = 0
    proc.communicate.return_value = ("", "")
    return proc


def test_run_ffmpeg_spreads_no_window(extractor):
    with (
        patch(f"{ME}.no_window_kwargs", return_value=SENTINEL),
        patch(f"{ME}.subprocess.Popen", return_value=_finished_popen()) as mpopen,
    ):
        extractor._run_ffmpeg(["ffmpeg", "-version"], "probe", timeout=5)
    assert mpopen.call_args.kwargs.get("creationflags") == 0x424242


def test_check_encoder_available_spreads_no_window(extractor):
    run_result = MagicMock(returncode=0, stdout="libsvtav1")
    with (
        patch(f"{ME}.no_window_kwargs", return_value=SENTINEL),
        patch(f"{ME}.subprocess.run", return_value=run_result) as mrun,
    ):
        extractor._check_encoder_available("libsvtav1")
    assert mrun.call_args.kwargs.get("creationflags") == 0x424242


def test_run_ffprobe_json_spreads_no_window():
    run_result = MagicMock(returncode=0, stdout="{}")
    with (
        patch(f"{ATD}.no_window_kwargs", return_value=SENTINEL),
        patch(f"{ATD}.subprocess.run", return_value=run_result) as mrun,
    ):
        _run_ffprobe_json(Path("video.mkv"), "a", "ffprobe")
    assert mrun.call_args.kwargs.get("creationflags") == 0x424242


def test_check_tool_spreads_no_window():
    run_result = MagicMock(returncode=0, stdout="ffmpeg version 6.0")
    with (
        patch(f"{VS}.no_window_kwargs", return_value=SENTINEL),
        patch(f"{VS}.subprocess.run", return_value=run_result) as mrun,
    ):
        ValidationService._check_tool("ffmpeg", "ffmpeg")
    assert mrun.call_args.kwargs.get("creationflags") == 0x424242
