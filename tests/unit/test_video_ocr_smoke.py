"""ANKI_MINER_SMOKE=videoocr: the smoke's verdicts, the seeder's videoocr code, the executed shell leg, the release wiring."""

from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from anki_miner.gui import app
from anki_miner.services.video_ocr.errors import EngineLoadError
from anki_miner.services.video_ocr.model_installer import DET_MODEL_NAME, REC_MODEL_NAME
from tests.unit.test_asr_smoke_leg import _fake_dist, _run_smoke, _seed_asr

_ROOT = Path(__file__).resolve().parents[2]
_READ = "anki_miner.services.video_ocr.scanner.read_region_text"
_MODELS = os.environ.get("ANKI_MINER_TEST_OCR_MODELS")
_needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")


def _seeder():
    spec = importlib.util.spec_from_file_location("seeder", _ROOT / "scripts" / "fetch_language_pack_seeds.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_smoke_passes_when_the_rendered_line_reads_back(capsys):
    with patch(_READ, return_value=app._VIDEO_OCR_SMOKE_TEXT) as read:
        assert app._run_video_ocr_bundled_smoke() == 0
    crop = read.call_args.args[1]
    # No top-level numpy import: test_asr_marker_gating would force the whole file into the asr job.
    assert crop.ndim == 3 and crop.shape[2] == 3 and crop.dtype.name == "uint8"
    assert "BUNDLED_SMOKE_PASS: videoocr" in capsys.readouterr().out


def test_smoke_fails_on_a_misread(capsys):
    with patch(_READ, return_value="こんにちわ"):
        assert app._run_video_ocr_bundled_smoke() == 1
    assert "BUNDLED_SMOKE_FAIL" in capsys.readouterr().err


def test_smoke_fails_loudly_when_the_engine_cannot_load(capsys):
    with patch(_READ, side_effect=EngineLoadError("no ort")):
        assert app._run_video_ocr_bundled_smoke() == 1
    assert "BUNDLED_SMOKE_FAIL" in capsys.readouterr().err


@pytest.mark.skipif(not _MODELS, reason="set ANKI_MINER_TEST_OCR_MODELS to a dir holding the two pinned models")
def test_smoke_reads_its_line_with_the_real_models():
    from anki_miner.services.video_ocr.meiki_engine import load_engine

    engine = load_engine(Path(str(_MODELS)))
    with patch("anki_miner.services.video_ocr.runtime.get_engine", return_value=engine):
        assert app._run_video_ocr_bundled_smoke() == 0


def _seed_video_ocr(seeds: Path, *, with_files: bool) -> None:
    runtime_dir = seeds / "videoocr" / "onnx_pack" / "onnxruntime"
    models = seeds / "videoocr" / "ocr_models"
    runtime_dir.mkdir(parents=True)
    models.mkdir(parents=True)
    if with_files:
        (runtime_dir / "__init__.py").write_bytes(b"")
        for name in (DET_MODEL_NAME, REC_MODEL_NAME):
            (models / name).write_bytes(b"onnx")


@_needs_bash
def test_a_seeded_leg_runs_the_app_and_prints_the_marker(tmp_path):
    dist = _fake_dist(tmp_path)
    record = tmp_path / "record.txt"
    seeds = tmp_path / "seeds"
    _seed_asr(seeds)
    _seed_video_ocr(seeds, with_files=True)
    result = _run_smoke(tmp_path, dist, record, seeds)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "videoocr" in record.read_text(encoding="utf-8").splitlines()
    assert "BUNDLED_VIDEO_OCR_PASS" in result.stdout


@_needs_bash
def test_seed_dirs_without_their_files_skip_instead_of_failing(tmp_path):
    # Both installers mkdir their root before the first byte, so a transport failure
    # mid-seed leaves empty directories; that must fall open, not fail the release.
    dist = _fake_dist(tmp_path)
    record = tmp_path / "record.txt"
    seeds = tmp_path / "seeds"
    _seed_asr(seeds)
    _seed_video_ocr(seeds, with_files=False)
    result = _run_smoke(tmp_path, dist, record, seeds)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SKIP videoocr" in result.stdout
    assert "videoocr" not in record.read_text(encoding="utf-8").splitlines()


def test_the_seeder_installs_the_pack_and_models_for_videoocr(tmp_path):
    seeder = _seeder()
    calls: list[Path] = []
    with (
        patch("anki_miner.services.asr.onnx_pack_installer.onnx_pack_supported", return_value=True),
        patch("anki_miner.services.asr.onnx_pack_installer.install_onnx_pack", side_effect=calls.append),
        patch("anki_miner.services.video_ocr.model_installer.install_models", side_effect=calls.append),
    ):
        seeder._install("videoocr", tmp_path / "videoocr")
    assert calls == [tmp_path / "videoocr" / "onnx_pack", tmp_path / "videoocr" / "ocr_models"]


def test_the_seeder_skips_videoocr_where_the_pack_is_unsupported(tmp_path):
    seeder = _seeder()
    with (
        patch("anki_miner.services.asr.onnx_pack_installer.onnx_pack_supported", return_value=False),
        patch("anki_miner.services.asr.onnx_pack_installer.install_onnx_pack") as install,
    ):
        seeder._install("videoocr", tmp_path / "videoocr")
    install.assert_not_called()


def test_release_seeds_videoocr_and_the_dry_run_refuses_a_skipped_leg():
    release = (_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    seed_line = next(line for line in release.splitlines() if "fetch_language_pack_seeds.py" in line)
    codes = seed_line.split('"$RUNNER_TEMP/lang_pack_seeds"', 1)[1].split()
    assert "videoocr" in codes and codes[-1] == "asr"
    smoke = (_ROOT / "scripts" / "bundle_smoke.sh").read_text(encoding="utf-8")
    assert "BUNDLED_VIDEO_OCR_PASS" in smoke and "SKIP videoocr" in smoke
    dryrun = (_ROOT / "scripts" / "release_dryrun.sh").read_text(encoding="utf-8")
    for leg in ("ubuntu-22.04", "windows-latest", "macos-latest"):
        assert f'assert_videoocr_ran "{leg}"' in dryrun
    assert 'assert_videoocr_ran "macos-15-intel"' not in dryrun  # no onnxruntime pack for Intel macs
