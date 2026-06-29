"""Tests for the in-app whisper.cpp ggml model installer.

Tests NEVER hit the network: ``download_to_temp`` is monkeypatched to write a
small fake ``.bin`` payload (a few bytes) into the staging dir, and each spec's
pinned sha256 is swapped for the digest of that fake payload so the verify path
passes. This keeps the suite off the multi-GB real downloads.
"""

from __future__ import annotations

import dataclasses
import hashlib
import threading
from pathlib import Path

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services.asr import ggml_model_installer as gmi

_FAKE_ACOUSTIC = b"fake-ggml-acoustic-bytes"
_FAKE_VAD = b"fake-ggml-vad-bytes"


def _retarget_acoustic(monkeypatch, asr_model: str, *, real_sha: bool = True) -> gmi._GgmlSpec:
    """Swap the acoustic spec for *asr_model* to the fake payload's digest.

    Returns the (possibly digest-corrected) spec so the caller can assert on its
    url. When ``real_sha`` is False the pinned sha is kept (mismatch path).
    """
    spec = gmi._ACOUSTIC_SPECS[asr_model]
    if real_sha:
        spec = dataclasses.replace(spec, sha256=hashlib.sha256(_FAKE_ACOUSTIC).hexdigest())
    specs = dict(gmi._ACOUSTIC_SPECS)
    specs[asr_model] = spec
    monkeypatch.setattr(gmi, "_ACOUSTIC_SPECS", specs)
    return spec


def _retarget_vad(monkeypatch, *, real_sha: bool = True) -> gmi._GgmlSpec:
    spec = gmi._VAD_SPEC
    if real_sha:
        spec = dataclasses.replace(spec, sha256=hashlib.sha256(_FAKE_VAD).hexdigest())
    monkeypatch.setattr(gmi, "_VAD_SPEC", spec)
    return spec


def _patch_download(monkeypatch, spec: gmi._GgmlSpec, payload: bytes):
    """Patch download_to_temp to write *payload* as the downloaded .part file."""

    def fake_download(url, *, dest_dir, progress=None, cancelled_check=None, max_bytes=None):
        assert url == spec.url
        # The cap must clear the largest real file.
        assert max_bytes is not None and max_bytes >= 1300 * 1024 * 1024
        if progress is not None:
            progress(0, len(payload), "downloading")
        dest_dir.mkdir(parents=True, exist_ok=True)
        part = dest_dir / "fake.part"
        part.write_bytes(payload)
        return part

    monkeypatch.setattr(gmi, "download_to_temp", fake_download)


class TestPathHelpers:
    def test_ggml_models_root_is_ggml_subdir(self, tmp_path):
        assert gmi.ggml_models_root(tmp_path) == tmp_path / "ggml"

    def test_model_map_resolves_large_v3(self, tmp_path):
        assert gmi.ggml_model_path("large-v3", tmp_path) == tmp_path / "ggml" / "ggml-large-v3-q5_0.bin"

    def test_model_map_resolves_small(self, tmp_path):
        assert gmi.ggml_model_path("small", tmp_path) == tmp_path / "ggml" / "ggml-small-q5_1.bin"

    def test_unknown_model_raises(self, tmp_path):
        with pytest.raises(SetupError):
            gmi.ggml_model_path("medium", tmp_path)

    def test_vad_model_path(self, tmp_path):
        assert gmi.vad_model_path(tmp_path) == tmp_path / "ggml" / "ggml-silero-v6.2.0.bin"

    def test_model_map_keys_are_known_models(self):
        from anki_miner.services.asr import model_manager

        assert set(gmi._ACOUSTIC_SPECS) == set(model_manager.KNOWN_MODELS)


class TestIsDownloaded:
    def test_false_on_empty_dir(self, tmp_path):
        assert gmi.is_ggml_downloaded("large-v3", tmp_path) is False

    def test_false_on_empty_file(self, tmp_path):
        path = gmi.ggml_model_path("small", tmp_path)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"")
        assert gmi.is_ggml_downloaded("small", tmp_path) is False

    def test_true_when_file_present(self, tmp_path):
        path = gmi.ggml_model_path("small", tmp_path)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"x" * 16)
        assert gmi.is_ggml_downloaded("small", tmp_path) is True

    def test_false_then_true_after_install(self, tmp_path, monkeypatch):
        spec = _retarget_acoustic(monkeypatch, "large-v3")
        _patch_download(monkeypatch, spec, _FAKE_ACOUSTIC)
        assert gmi.is_ggml_downloaded("large-v3", tmp_path) is False
        gmi.install_ggml_model("large-v3", tmp_path)
        assert gmi.is_ggml_downloaded("large-v3", tmp_path) is True

    def test_vad_false_then_true(self, tmp_path, monkeypatch):
        spec = _retarget_vad(monkeypatch)
        _patch_download(monkeypatch, spec, _FAKE_VAD)
        assert gmi.is_vad_downloaded(tmp_path) is False
        gmi.install_vad_model(tmp_path)
        assert gmi.is_vad_downloaded(tmp_path) is True


class TestInstallAcoustic:
    def test_happy_path_stages_under_ggml(self, tmp_path, monkeypatch):
        spec = _retarget_acoustic(monkeypatch, "large-v3")
        _patch_download(monkeypatch, spec, _FAKE_ACOUSTIC)

        result = gmi.install_ggml_model("large-v3", tmp_path)

        expected = tmp_path / "ggml" / "ggml-large-v3-q5_0.bin"
        assert result == expected
        assert expected.read_bytes() == _FAKE_ACOUSTIC
        assert gmi.is_ggml_downloaded("large-v3", tmp_path) is True

    def test_small_resolves_right_filename(self, tmp_path, monkeypatch):
        spec = _retarget_acoustic(monkeypatch, "small")
        _patch_download(monkeypatch, spec, _FAKE_ACOUSTIC)
        result = gmi.install_ggml_model("small", tmp_path)
        assert result == tmp_path / "ggml" / "ggml-small-q5_1.bin"
        assert result.read_bytes() == _FAKE_ACOUSTIC

    def test_skips_when_already_present(self, tmp_path, monkeypatch):
        # Pre-stage the file; download must NOT be called.
        path = gmi.ggml_model_path("large-v3", tmp_path)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"already-here")

        def boom(*a, **k):  # pragma: no cover - must not run
            raise AssertionError("download_to_temp should not be called when present")

        monkeypatch.setattr(gmi, "download_to_temp", boom)
        result = gmi.install_ggml_model("large-v3", tmp_path)
        assert result == path
        assert path.read_bytes() == b"already-here"

    def test_returns_path_type(self, tmp_path, monkeypatch):
        spec = _retarget_acoustic(monkeypatch, "small")
        _patch_download(monkeypatch, spec, _FAKE_ACOUSTIC)
        assert isinstance(gmi.install_ggml_model("small", tmp_path), Path)

    def test_no_part_files_left_behind(self, tmp_path, monkeypatch):
        spec = _retarget_acoustic(monkeypatch, "small")
        _patch_download(monkeypatch, spec, _FAKE_ACOUSTIC)
        gmi.install_ggml_model("small", tmp_path)
        assert list((tmp_path / "ggml").glob("*.part")) == []

    def test_progress_forwarded(self, tmp_path, monkeypatch):
        spec = _retarget_acoustic(monkeypatch, "small")
        _patch_download(monkeypatch, spec, _FAKE_ACOUSTIC)
        calls: list[tuple] = []
        gmi.install_ggml_model("small", tmp_path, progress=lambda d, t, m: calls.append((d, t, m)))
        assert calls

    def test_install_sweeps_orphans(self, tmp_path, monkeypatch):
        spec = _retarget_acoustic(monkeypatch, "small")
        _patch_download(monkeypatch, spec, _FAKE_ACOUSTIC)
        ggml_dir = tmp_path / "ggml"
        ggml_dir.mkdir(parents=True)
        (ggml_dir / "leftover.part").write_bytes(b"x" * 1024)
        gmi.install_ggml_model("small", tmp_path)
        assert list(ggml_dir.glob("*.part")) == []
        assert gmi.is_ggml_downloaded("small", tmp_path) is True

    def test_unknown_model_raises(self, tmp_path, monkeypatch):
        with pytest.raises(SetupError):
            gmi.install_ggml_model("medium", tmp_path)

    def test_sha_mismatch_raises_and_promotes_nothing(self, tmp_path, monkeypatch):
        spec = _retarget_acoustic(monkeypatch, "large-v3", real_sha=False)  # keep pinned sha
        _patch_download(monkeypatch, spec, _FAKE_ACOUSTIC)
        with pytest.raises(SetupError, match="checksum"):
            gmi.install_ggml_model("large-v3", tmp_path)
        assert not (tmp_path / "ggml" / "ggml-large-v3-q5_0.bin").exists()
        assert list((tmp_path / "ggml").glob("*.part")) == []


class TestInstallVad:
    def test_happy_path(self, tmp_path, monkeypatch):
        spec = _retarget_vad(monkeypatch)
        _patch_download(monkeypatch, spec, _FAKE_VAD)
        result = gmi.install_vad_model(tmp_path)
        expected = tmp_path / "ggml" / "ggml-silero-v6.2.0.bin"
        assert result == expected
        assert expected.read_bytes() == _FAKE_VAD
        assert gmi.is_vad_downloaded(tmp_path) is True

    def test_skips_when_already_present(self, tmp_path, monkeypatch):
        path = gmi.vad_model_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"vad-here")

        def boom(*a, **k):  # pragma: no cover - must not run
            raise AssertionError("download_to_temp should not run when present")

        monkeypatch.setattr(gmi, "download_to_temp", boom)
        assert gmi.install_vad_model(tmp_path) == path

    def test_sha_mismatch_raises_and_promotes_nothing(self, tmp_path, monkeypatch):
        spec = _retarget_vad(monkeypatch, real_sha=False)
        _patch_download(monkeypatch, spec, _FAKE_VAD)
        with pytest.raises(SetupError, match="checksum"):
            gmi.install_vad_model(tmp_path)
        assert not (tmp_path / "ggml" / "ggml-silero-v6.2.0.bin").exists()
        assert list((tmp_path / "ggml").glob("*.part")) == []


class TestCancel:
    def test_cancel_before_download_acoustic(self, tmp_path, monkeypatch):
        spec = _retarget_acoustic(monkeypatch, "large-v3")
        _patch_download(monkeypatch, spec, _FAKE_ACOUSTIC)
        ev = threading.Event()
        ev.set()
        with pytest.raises(SetupError, match="cancel"):
            gmi.install_ggml_model("large-v3", tmp_path, cancel_event=ev)
        assert not (tmp_path / "ggml" / "ggml-large-v3-q5_0.bin").exists()

    def test_cancel_after_download_leaves_no_promoted_file(self, tmp_path, monkeypatch):
        # Retarget so the digest would pass — the cancel must abort first regardless.
        _retarget_acoustic(monkeypatch, "large-v3")
        ev = threading.Event()

        def fake_download(url, *, dest_dir, progress=None, cancelled_check=None, max_bytes=None):
            dest_dir.mkdir(parents=True, exist_ok=True)
            part = dest_dir / "fake.part"
            part.write_bytes(_FAKE_ACOUSTIC)
            ev.set()  # trip the post-download cancel check
            return part

        monkeypatch.setattr(gmi, "download_to_temp", fake_download)
        with pytest.raises(SetupError, match="cancel"):
            gmi.install_ggml_model("large-v3", tmp_path, cancel_event=ev)
        assert not (tmp_path / "ggml" / "ggml-large-v3-q5_0.bin").exists()
        assert list((tmp_path / "ggml").glob("*.part")) == []

    def test_cancel_before_download_vad(self, tmp_path, monkeypatch):
        spec = _retarget_vad(monkeypatch)
        _patch_download(monkeypatch, spec, _FAKE_VAD)
        ev = threading.Event()
        ev.set()
        with pytest.raises(SetupError, match="cancel"):
            gmi.install_vad_model(tmp_path, cancel_event=ev)
        assert not (tmp_path / "ggml" / "ggml-silero-v6.2.0.bin").exists()
