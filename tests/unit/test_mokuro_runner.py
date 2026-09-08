"""Tests for MokuroRunnerService — argv, env, output parsing, the success rule."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions.mokuro import MokuroError, MokuroNotFoundError
from anki_miner.services import mokuro_runner as mr
from anki_miner.services.mokuro_volumes import MokuroVolume
from anki_miner.utils.process_supervisor import SupervisedResult, SupervisedState

_OK_LINES = [
    "2026-09-07 10:00:00.000 | INFO     | mokuro.run:run:128 - Processing 1/1: /m/vol1",
    "Processing pages...:   0%|          | 0/25 [00:00<?, ?it/s]",
    "Processing pages...:  12%|█▏        | 3/25 [00:05<00:40,  1.85s/it]",
    "Processing pages...: 100%|██████████| 25/25 [00:40<00:00,  1.60s/it]",
    "2026-09-07 10:01:00.000 | INFO     | mokuro.run:run:140 - Processed successfully: 1/1",
]


def _volume(tmp_path: Path) -> MokuroVolume:
    src = tmp_path / "vol1"
    src.mkdir()
    return MokuroVolume(source=src, output=tmp_path / "vol1.mokuro", already_processed=False)


def _fake_run(
    lines: list[str], state=SupervisedState.COMPLETED, returncode=0, error=None, *, write_output: Path | None = None
):
    calls: dict[str, Any] = {}

    def fake(cmd, **kwargs):
        calls["cmd"] = [str(c) for c in cmd]
        calls["kwargs"] = kwargs
        cb = kwargs.get("line_callback")
        for line in lines:
            if cb is not None:
                cb(line)
        if write_output is not None:
            write_output.write_text("{}")
        return SupervisedResult(state, returncode, "", "", error)

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


@pytest.fixture
def config(tmp_path):
    return AnkiMinerConfig(media_temp_folder=tmp_path / "tmp", uv_root=tmp_path / "uv")


def test_success_needs_receipt_and_output(monkeypatch, tmp_path, config):
    vol = _volume(tmp_path)
    fake = _fake_run(_OK_LINES, write_output=vol.output)
    monkeypatch.setattr(mr, "run_supervised", fake)
    result = mr.MokuroRunnerService(config).process_volume(vol, mr.MokuroOptions())
    assert result == mr.MokuroResult(mr.MokuroStatus.DONE, vol.output)


def test_argv_has_path_first_then_explicit_flags(monkeypatch, tmp_path, config):
    vol = _volume(tmp_path)
    fake = _fake_run(_OK_LINES, write_output=vol.output)
    monkeypatch.setattr(mr, "run_supervised", fake)
    monkeypatch.setattr(mr, "resolve_mokuro", lambda cfg: "/x/mokuro")
    mr.MokuroRunnerService(config).process_volume(vol, mr.MokuroOptions(force_cpu=True, no_cache=True))
    assert fake.calls["cmd"] == [
        "/x/mokuro",
        str(vol.source),
        "--disable_confirmation=True",
        "--ignore_errors=True",
        "--legacy_html=False",
        "--force_cpu=True",
        "--no_cache=True",
    ]
    kwargs = fake.calls["kwargs"]
    assert kwargs["treat_cr_as_newline"] is True
    assert kwargs["combine_stderr"] is True
    assert kwargs["noise_filter"] is mr.is_progress_only
    assert kwargs["env"]["PYTHONUTF8"] == "1" and kwargs["env"]["PYTHONIOENCODING"] == "utf-8"


def test_default_options_pass_no_gpu_or_cache_flags(monkeypatch, tmp_path, config):
    vol = _volume(tmp_path)
    fake = _fake_run(_OK_LINES, write_output=vol.output)
    monkeypatch.setattr(mr, "run_supervised", fake)
    mr.MokuroRunnerService(config).process_volume(vol, mr.MokuroOptions())
    assert not any(f.startswith(("--force_cpu", "--no_cache")) for f in fake.calls["cmd"])


def test_page_ticks_become_fractional_progress(monkeypatch, tmp_path, config):
    vol = _volume(tmp_path)
    monkeypatch.setattr(mr, "run_supervised", _fake_run(_OK_LINES, write_output=vol.output))
    seen: list[tuple[str, float | None]] = []
    mr.MokuroRunnerService(config).process_volume(vol, mr.MokuroOptions(), progress_cb=lambda m, f: seen.append((m, f)))
    assert ("Page 3 of 25", 3 / 25) in seen
    assert ("Page 25 of 25", 1.0) in seen


def test_model_download_and_device_lines_become_status(monkeypatch, tmp_path, config):
    vol = _volume(tmp_path)
    lines = [
        "2026-09-07 | INFO | mokuro.cache:_download_if_needed:22 - Downloading https://github.com/x/comictextdetector.pt",
        "2026-09-07 | INFO | mokuro.manga_page_ocr:__init__:40 - Initializing text detector, using device cuda",
        *_OK_LINES,
    ]
    monkeypatch.setattr(mr, "run_supervised", _fake_run(lines, write_output=vol.output))
    seen: list[tuple[str, float | None]] = []
    mr.MokuroRunnerService(config).process_volume(vol, mr.MokuroOptions(), progress_cb=lambda m, f: seen.append((m, f)))
    assert ("Downloading OCR models (first run only)", None) in seen
    assert ("Loading models (cuda)", None) in seen


def test_exit_zero_without_receipt_is_a_failure(monkeypatch, tmp_path, config):
    vol = _volume(tmp_path)
    lines = ["2026-09-07 | ERROR | mokuro.run:run:70 - Invalid path: /m/vol1"]
    monkeypatch.setattr(mr, "run_supervised", _fake_run(lines))
    with pytest.raises(MokuroError, match="Invalid path"):
        mr.MokuroRunnerService(config).process_volume(vol, mr.MokuroOptions())


def test_volume_error_line_is_a_failure_even_with_receipt(monkeypatch, tmp_path, config):
    vol = _volume(tmp_path)
    lines = ["2026-09-07 | ERROR | mokuro.run:run:150 - Error while processing /m/vol1", *_OK_LINES]
    monkeypatch.setattr(mr, "run_supervised", _fake_run(lines, write_output=vol.output))
    with pytest.raises(MokuroError):
        mr.MokuroRunnerService(config).process_volume(vol, mr.MokuroOptions())


def test_receipt_without_output_file_is_a_failure(monkeypatch, tmp_path, config):
    vol = _volume(tmp_path)
    monkeypatch.setattr(mr, "run_supervised", _fake_run(_OK_LINES))
    with pytest.raises(MokuroError, match="vol1.mokuro"):
        mr.MokuroRunnerService(config).process_volume(vol, mr.MokuroOptions())


def test_missing_binary_raises_not_found(monkeypatch, tmp_path, config):
    vol = _volume(tmp_path)
    monkeypatch.setattr(mr, "run_supervised", _fake_run([], SupervisedState.FAILED, None, FileNotFoundError("mokuro")))
    with pytest.raises(MokuroNotFoundError):
        mr.MokuroRunnerService(config).process_volume(vol, mr.MokuroOptions())


def test_cancelled_returns_cancelled(monkeypatch, tmp_path, config):
    vol = _volume(tmp_path)
    monkeypatch.setattr(mr, "run_supervised", _fake_run([], SupervisedState.CANCELLED, None))
    result = mr.MokuroRunnerService(config).process_volume(vol, mr.MokuroOptions())
    assert result.status is mr.MokuroStatus.CANCELLED and result.output_path is None


@pytest.mark.parametrize("state", [SupervisedState.TIMED_OUT, SupervisedState.FAILED])
def test_timeout_and_nonzero_exit_raise(monkeypatch, tmp_path, config, state):
    vol = _volume(tmp_path)
    monkeypatch.setattr(mr, "run_supervised", _fake_run(["Traceback: boom"], state, 1))
    with pytest.raises(MokuroError):
        mr.MokuroRunnerService(config).process_volume(vol, mr.MokuroOptions())


def test_nonzero_exit_failure_message_includes_the_tail(monkeypatch, tmp_path, config):
    vol = _volume(tmp_path)
    monkeypatch.setattr(mr, "run_supervised", _fake_run(["Traceback: boom"], SupervisedState.FAILED, 1))
    with pytest.raises(MokuroError, match="boom"):
        mr.MokuroRunnerService(config).process_volume(vol, mr.MokuroOptions())


def test_spawn_oserror_without_returncode_is_a_failure(monkeypatch, tmp_path, config):
    vol = _volume(tmp_path)
    error = OSError(8, "Exec format error")
    monkeypatch.setattr(mr, "run_supervised", _fake_run([], SupervisedState.FAILED, None, error))
    with pytest.raises(MokuroError, match="Exec format error"):
        mr.MokuroRunnerService(config).process_volume(vol, mr.MokuroOptions())


def test_progress_only_filter():
    assert mr.is_progress_only("Processing pages...:  12%|█▏        | 3/25 [00:05<00:40]")
    assert not mr.is_progress_only("2026 | INFO | mokuro.run:run:128 - Processing 1/1: /m")


_HOST_PYTHON_VARS = ("VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONHOME", "PYTHONPATH")


def test_child_env_drops_the_host_python_selection(monkeypatch):
    """PYTHONHOME/PYTHONPATH from the launching shell re-point the venv's mokuro
    at the host's site-packages; the installer already scrubs them for uv."""
    for name in _HOST_PYTHON_VARS:
        monkeypatch.setenv(name, "/host")
    env = mr.MokuroRunnerService._child_env()
    assert not set(_HOST_PYTHON_VARS) & env.keys()
    assert env["PYTHONUTF8"] == "1" and env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUNBUFFERED"] == "1" and env["NO_COLOR"] == "1"
