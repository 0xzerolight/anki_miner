"""Tests for the in-app CUDA library pack installer.

Tests NEVER hit the network: ``download_to_temp`` is monkeypatched to write a
fake in-memory wheel (a real zip) into the staging dir. The platform selector is
monkeypatched so behaviour is deterministic on the Linux CI runner.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import threading
import zipfile
from pathlib import Path

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services.asr import cuda_pack_installer


def _make_wheel(members: dict[str, bytes]) -> bytes:
    """Return zip bytes containing *members* (arcname -> contents)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for arcname, data in members.items():
            zf.writestr(arcname, data)
    return buf.getvalue()


# Fake Linux wheels: cuDNN (loader + one sub-lib) and cuBLAS (cublas + cublasLt).
_CUDNN_WHEEL = _make_wheel(
    {
        "nvidia/cudnn/lib/libcudnn.so.9": b"cudnn-loader",
        "nvidia/cudnn/lib/libcudnn_ops.so.9": b"cudnn-ops",
        "nvidia/cudnn/__init__.py": b"# not a lib, must be ignored",
    }
)
_CUBLAS_WHEEL = _make_wheel(
    {
        "nvidia/cublas/lib/libcublas.so.12": b"cublas",
        "nvidia/cublas/lib/libcublasLt.so.12": b"cublasLt",
        "nvidia/cublas/lib/.gitkeep": b"",
    }
)


def _force_linux(monkeypatch) -> None:
    monkeypatch.setattr(cuda_pack_installer.sys, "platform", "linux")


def _retarget_specs(monkeypatch, *, real_sha: bool) -> dict[str, bytes]:
    """Replace the module's Linux specs with copies and return url -> wheel map.

    Specs are frozen dataclasses, so we swap the whole module-level spec object
    via ``dataclasses.replace``. When ``real_sha`` is True the copy's sha256 is
    set to the actual digest of the fake wheel so the verify path passes;
    otherwise the pinned sha is kept (to exercise the mismatch path).
    """
    cudnn = cuda_pack_installer._LINUX_CUDNN_SPEC
    cublas = cuda_pack_installer._LINUX_CUBLAS_SPEC
    if real_sha:
        cudnn = dataclasses.replace(cudnn, sha256=hashlib.sha256(_CUDNN_WHEEL).hexdigest())
        cublas = dataclasses.replace(cublas, sha256=hashlib.sha256(_CUBLAS_WHEEL).hexdigest())
    monkeypatch.setattr(cuda_pack_installer, "_LINUX_CUDNN_SPEC", cudnn)
    monkeypatch.setattr(cuda_pack_installer, "_LINUX_CUBLAS_SPEC", cublas)
    return {cudnn.url: _CUDNN_WHEEL, cublas.url: _CUBLAS_WHEEL}


def _patch_download(monkeypatch, *, sha_real: bool = True):
    """Patch download_to_temp to write the right fake wheel per component."""
    wheels = _retarget_specs(monkeypatch, real_sha=sha_real)

    def fake_download(url, *, dest_dir, progress=None, cancelled_check=None, max_bytes=None):
        if progress is not None:
            progress(0, len(wheels[url]), "downloading")
        dest_dir.mkdir(parents=True, exist_ok=True)
        part = dest_dir / "fake.part"
        part.write_bytes(wheels[url])
        return part

    monkeypatch.setattr(cuda_pack_installer, "download_to_temp", fake_download)


class TestSupported:
    def test_supported_on_linux(self, monkeypatch):
        monkeypatch.setattr(cuda_pack_installer.sys, "platform", "linux")
        assert cuda_pack_installer.cuda_pack_supported() is True

    def test_supported_on_windows(self, monkeypatch):
        monkeypatch.setattr(cuda_pack_installer.sys, "platform", "win32")
        assert cuda_pack_installer.cuda_pack_supported() is True

    def test_unsupported_on_macos(self, monkeypatch):
        monkeypatch.setattr(cuda_pack_installer.sys, "platform", "darwin")
        assert cuda_pack_installer.cuda_pack_supported() is False


class TestIsInstalled:
    def test_false_on_empty_dir(self, tmp_path, monkeypatch):
        _force_linux(monkeypatch)
        assert cuda_pack_installer.is_installed(tmp_path) is False

    def test_false_when_only_one_component(self, tmp_path, monkeypatch):
        _force_linux(monkeypatch)
        (tmp_path / "cudnn").mkdir()
        (tmp_path / "cudnn" / "libcudnn.so.9").write_bytes(b"x")
        assert cuda_pack_installer.is_installed(tmp_path) is False

    def test_true_when_both_components_present_linux(self, tmp_path, monkeypatch):
        _force_linux(monkeypatch)
        (tmp_path / "cudnn").mkdir()
        (tmp_path / "cublas").mkdir()
        (tmp_path / "cudnn" / "libcudnn.so.9").write_bytes(b"x")
        (tmp_path / "cublas" / "libcublas.so.12").write_bytes(b"y")
        assert cuda_pack_installer.is_installed(tmp_path) is True

    def test_true_with_minor_filename_variation(self, tmp_path, monkeypatch):
        _force_linux(monkeypatch)
        (tmp_path / "cudnn").mkdir()
        (tmp_path / "cublas").mkdir()
        (tmp_path / "cudnn" / "libcudnn.so.9.23.2").write_bytes(b"x")
        (tmp_path / "cublas" / "libcublas.so.12.9.2").write_bytes(b"y")
        assert cuda_pack_installer.is_installed(tmp_path) is True


class TestInstall:
    def test_happy_path_extracts_flattened(self, tmp_path, monkeypatch):
        _force_linux(monkeypatch)
        _patch_download(monkeypatch)

        result = cuda_pack_installer.install_cuda_pack(tmp_path)

        assert result == tmp_path
        assert (tmp_path / "cudnn" / "libcudnn.so.9").read_bytes() == b"cudnn-loader"
        assert (tmp_path / "cudnn" / "libcudnn_ops.so.9").read_bytes() == b"cudnn-ops"
        assert (tmp_path / "cublas" / "libcublas.so.12").read_bytes() == b"cublas"
        assert (tmp_path / "cublas" / "libcublasLt.so.12").read_bytes() == b"cublasLt"
        # Non-lib members are not extracted.
        assert not (tmp_path / "cudnn" / "__init__.py").exists()
        assert cuda_pack_installer.is_installed(tmp_path) is True

    def test_no_part_files_left_behind(self, tmp_path, monkeypatch):
        _force_linux(monkeypatch)
        _patch_download(monkeypatch)
        cuda_pack_installer.install_cuda_pack(tmp_path)
        assert list(tmp_path.glob("*.part")) == []

    def test_progress_forwarded(self, tmp_path, monkeypatch):
        _force_linux(monkeypatch)
        _patch_download(monkeypatch)
        calls: list[tuple] = []
        cuda_pack_installer.install_cuda_pack(tmp_path, progress=lambda d, t, m: calls.append((d, t, m)))
        assert calls  # at least one progress event from each download

    def test_sha_mismatch_raises_and_promotes_nothing(self, tmp_path, monkeypatch):
        _force_linux(monkeypatch)
        _patch_download(monkeypatch, sha_real=False)  # specs keep their real pinned shas
        with pytest.raises(SetupError, match="checksum"):
            cuda_pack_installer.install_cuda_pack(tmp_path)
        assert not (tmp_path / "cudnn").exists()
        assert list(tmp_path.glob("*.part")) == []

    def test_bad_zip_raises(self, tmp_path, monkeypatch):
        _force_linux(monkeypatch)

        def bad_download(url, *, dest_dir, progress=None, cancelled_check=None, max_bytes=None):
            dest_dir.mkdir(parents=True, exist_ok=True)
            part = dest_dir / "bad.part"
            part.write_bytes(b"not a zip")
            return part

        monkeypatch.setattr(cuda_pack_installer, "download_to_temp", bad_download)
        monkeypatch.setattr(
            cuda_pack_installer,
            "_LINUX_CUDNN_SPEC",
            dataclasses.replace(
                cuda_pack_installer._LINUX_CUDNN_SPEC,
                sha256=hashlib.sha256(b"not a zip").hexdigest(),
            ),
        )
        with pytest.raises(SetupError):
            cuda_pack_installer.install_cuda_pack(tmp_path)


class TestCancel:
    def test_cancel_before_any_download_raises_nothing_promoted(self, tmp_path, monkeypatch):
        _force_linux(monkeypatch)
        _patch_download(monkeypatch)
        ev = threading.Event()
        ev.set()
        with pytest.raises(SetupError, match="cancel"):
            cuda_pack_installer.install_cuda_pack(tmp_path, cancel_event=ev)
        assert not (tmp_path / "cudnn").exists()
        assert not (tmp_path / "cublas").exists()

    def test_cancel_after_download_leaves_no_promoted_dir(self, tmp_path, monkeypatch):
        _force_linux(monkeypatch)
        # Set the event the moment the first download returns, so the post-
        # download cancel check trips before any extraction/promotion.
        ev = threading.Event()
        cudnn_sha = hashlib.sha256(_CUDNN_WHEEL).hexdigest()
        monkeypatch.setattr(
            cuda_pack_installer,
            "_LINUX_CUDNN_SPEC",
            dataclasses.replace(cuda_pack_installer._LINUX_CUDNN_SPEC, sha256=cudnn_sha),
        )

        def fake_download(url, *, dest_dir, progress=None, cancelled_check=None, max_bytes=None):
            dest_dir.mkdir(parents=True, exist_ok=True)
            part = dest_dir / "fake.part"
            part.write_bytes(_CUDNN_WHEEL)
            ev.set()
            return part

        monkeypatch.setattr(cuda_pack_installer, "download_to_temp", fake_download)
        with pytest.raises(SetupError, match="cancel"):
            cuda_pack_installer.install_cuda_pack(tmp_path, cancel_event=ev)
        assert not (tmp_path / "cudnn").exists()
        assert list(tmp_path.glob("*.part")) == []


class TestUnsupportedPlatform:
    def test_install_on_macos_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cuda_pack_installer.sys, "platform", "darwin")
        with pytest.raises(SetupError):
            cuda_pack_installer.install_cuda_pack(tmp_path)


def test_install_returns_path_type(tmp_path, monkeypatch):
    _force_linux(monkeypatch)
    _patch_download(monkeypatch)
    result = cuda_pack_installer.install_cuda_pack(tmp_path)
    assert isinstance(result, Path)
