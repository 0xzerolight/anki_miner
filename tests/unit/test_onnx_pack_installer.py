"""Tests for the in-app onnxruntime (VAD) pack installer.

Tests NEVER hit the network: ``download_to_temp`` is monkeypatched to write a
fake in-memory wheel (a real zip) into the staging dir. The platform/Python
selector is monkeypatched so behaviour is deterministic on any CI runner.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import sys
import threading
import zipfile
from pathlib import Path

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services.asr import onnx_pack_installer


def _make_wheel(members: dict[str, bytes]) -> bytes:
    """Return zip bytes containing *members* (arcname -> contents)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for arcname, data in members.items():
            zf.writestr(arcname, data)
    return buf.getvalue()


# A fake onnxruntime wheel: the package tree plus a dist-info that must be skipped.
_ORT_WHEEL = _make_wheel(
    {
        "onnxruntime/__init__.py": b"# ort package",
        "onnxruntime/capi/_pybind_state.py": b"state",
        "onnxruntime/capi/libonnxruntime.so": b"native-lib",
        "onnxruntime-1.27.0.dist-info/METADATA": b"meta",  # must NOT be extracted
        "onnxruntime-1.27.0.dist-info/RECORD": b"record",
    }
)


def _force_supported_linux(monkeypatch, *, sha_real: bool = True) -> onnx_pack_installer._OnnxWheelSpec:
    """Make ``_current_spec`` resolve to a linux/x86_64 spec for the fake wheel."""
    monkeypatch.setattr(onnx_pack_installer, "_BUNDLE_PYTHON", sys.version_info[:2])
    monkeypatch.setattr(onnx_pack_installer.sys, "platform", "linux")
    monkeypatch.setattr(onnx_pack_installer.platform, "machine", lambda: "x86_64")
    spec = onnx_pack_installer._WHEELS[("linux", "x86_64")]
    if sha_real:
        spec = dataclasses.replace(spec, sha256=hashlib.sha256(_ORT_WHEEL).hexdigest())
    wheels = dict(onnx_pack_installer._WHEELS)
    wheels[("linux", "x86_64")] = spec
    monkeypatch.setattr(onnx_pack_installer, "_WHEELS", wheels)
    return spec


def _patch_download(monkeypatch, spec, payload: bytes = _ORT_WHEEL):
    """Patch download_to_temp to write *payload* as the downloaded .part wheel."""

    def fake_download(url, *, dest_dir, progress=None, cancelled_check=None, max_bytes=None):
        assert url == spec.url
        if progress is not None:
            progress(0, len(payload), "downloading")
        dest_dir.mkdir(parents=True, exist_ok=True)
        part = dest_dir / "fake.part"
        part.write_bytes(payload)
        return part

    monkeypatch.setattr(onnx_pack_installer, "download_to_temp", fake_download)


class TestSupported:
    def test_supported_on_linux_x86_64_bundle_python(self, monkeypatch):
        monkeypatch.setattr(onnx_pack_installer, "_BUNDLE_PYTHON", sys.version_info[:2])
        monkeypatch.setattr(onnx_pack_installer.sys, "platform", "linux")
        monkeypatch.setattr(onnx_pack_installer.platform, "machine", lambda: "x86_64")
        assert onnx_pack_installer.onnx_pack_supported() is True

    def test_unsupported_on_wrong_python(self, monkeypatch):
        # A Python the bundle never ships → wheel would be ABI-incompatible.
        monkeypatch.setattr(onnx_pack_installer, "_BUNDLE_PYTHON", (3, 99))
        monkeypatch.setattr(onnx_pack_installer.sys, "platform", "linux")
        monkeypatch.setattr(onnx_pack_installer.platform, "machine", lambda: "x86_64")
        assert onnx_pack_installer.onnx_pack_supported() is False

    def test_unsupported_on_unknown_arch(self, monkeypatch):
        monkeypatch.setattr(onnx_pack_installer, "_BUNDLE_PYTHON", sys.version_info[:2])
        monkeypatch.setattr(onnx_pack_installer.sys, "platform", "linux")
        monkeypatch.setattr(onnx_pack_installer.platform, "machine", lambda: "ppc64le")
        assert onnx_pack_installer.onnx_pack_supported() is False

    def test_unsupported_on_intel_macos(self, monkeypatch):
        monkeypatch.setattr(onnx_pack_installer, "_BUNDLE_PYTHON", sys.version_info[:2])
        monkeypatch.setattr(onnx_pack_installer.sys, "platform", "darwin")
        monkeypatch.setattr(onnx_pack_installer.platform, "machine", lambda: "x86_64")
        assert onnx_pack_installer.onnx_pack_supported() is False


class TestIsInstalled:
    def test_false_on_empty_dir(self, tmp_path):
        assert onnx_pack_installer.is_installed(tmp_path) is False

    def test_false_without_init(self, tmp_path):
        (tmp_path / "onnxruntime").mkdir()
        assert onnx_pack_installer.is_installed(tmp_path) is False

    def test_true_when_package_present(self, tmp_path):
        (tmp_path / "onnxruntime").mkdir()
        (tmp_path / "onnxruntime" / "__init__.py").write_bytes(b"x")
        assert onnx_pack_installer.is_installed(tmp_path) is True


class TestInstall:
    def test_happy_path_extracts_tree(self, tmp_path, monkeypatch):
        spec = _force_supported_linux(monkeypatch)
        _patch_download(monkeypatch, spec)

        result = onnx_pack_installer.install_onnx_pack(tmp_path)

        assert result == tmp_path
        pkg = tmp_path / "onnxruntime"
        assert (pkg / "__init__.py").read_bytes() == b"# ort package"
        # Structure is preserved (not flattened).
        assert (pkg / "capi" / "_pybind_state.py").read_bytes() == b"state"
        assert (pkg / "capi" / "libonnxruntime.so").read_bytes() == b"native-lib"
        # dist-info metadata is NOT extracted.
        assert not (tmp_path / "onnxruntime-1.27.0.dist-info").exists()
        assert onnx_pack_installer.is_installed(tmp_path) is True

    def test_no_part_files_left_behind(self, tmp_path, monkeypatch):
        spec = _force_supported_linux(monkeypatch)
        _patch_download(monkeypatch, spec)
        onnx_pack_installer.install_onnx_pack(tmp_path)
        assert list(tmp_path.glob("*.part")) == []

    def test_install_sweeps_orphans_from_a_crashed_run(self, tmp_path, monkeypatch):
        spec = _force_supported_linux(monkeypatch)
        _patch_download(monkeypatch, spec)
        # Orphans a SIGKILL'd prior install left in the root.
        (tmp_path / "leftover.part").write_bytes(b"x" * 1024)
        orphan_staging = tmp_path / ".staging-onnx-abcd"
        orphan_staging.mkdir()
        (orphan_staging / "junk").write_bytes(b"junk")

        onnx_pack_installer.install_onnx_pack(tmp_path)

        assert list(tmp_path.glob("*.part")) == []
        assert list(tmp_path.glob(".staging-*")) == []
        assert onnx_pack_installer.is_installed(tmp_path) is True

    def test_progress_forwarded_with_component_label(self, tmp_path, monkeypatch):
        spec = _force_supported_linux(monkeypatch)
        _patch_download(monkeypatch, spec)
        calls: list[tuple] = []
        onnx_pack_installer.install_onnx_pack(tmp_path, progress=lambda d, t, m: calls.append((d, t, m)))
        assert calls
        assert all(msg == "onnxruntime: downloading" for _, _, msg in calls)

    def test_reinstall_replaces_existing(self, tmp_path, monkeypatch):
        # A stale file under onnxruntime/ must be gone after a fresh install.
        (tmp_path / "onnxruntime").mkdir()
        (tmp_path / "onnxruntime" / "stale.py").write_bytes(b"old")
        spec = _force_supported_linux(monkeypatch)
        _patch_download(monkeypatch, spec)
        onnx_pack_installer.install_onnx_pack(tmp_path)
        assert not (tmp_path / "onnxruntime" / "stale.py").exists()
        assert (tmp_path / "onnxruntime" / "__init__.py").exists()

    def test_reinstall_fault_preserves_existing(self, tmp_path, monkeypatch):
        import anki_miner.utils.atomic_io as atomic_io

        target = tmp_path / "onnxruntime"
        target.mkdir()
        (target / "__init__.py").write_bytes(b"old package")
        spec = _force_supported_linux(monkeypatch)
        _patch_download(monkeypatch, spec)
        real_replace = atomic_io.os.replace

        def fail_promotion(src, dst):
            if Path(src).parent.name.startswith(".staging-onnx-") and Path(dst) == target:
                raise OSError("promotion fault")
            return real_replace(src, dst)

        monkeypatch.setattr(atomic_io.os, "replace", fail_promotion)

        with pytest.raises(OSError, match="promotion fault"):
            onnx_pack_installer.install_onnx_pack(tmp_path)

        assert (target / "__init__.py").read_bytes() == b"old package"

    def test_sha_mismatch_raises_and_promotes_nothing(self, tmp_path, monkeypatch):
        spec = _force_supported_linux(monkeypatch, sha_real=False)  # keep pinned sha
        _patch_download(monkeypatch, spec)
        with pytest.raises(SetupError, match="checksum"):
            onnx_pack_installer.install_onnx_pack(tmp_path)
        assert not (tmp_path / "onnxruntime").exists()
        assert list(tmp_path.glob("*.part")) == []

    def test_bad_zip_raises(self, tmp_path, monkeypatch):
        payload = b"not a zip"
        monkeypatch.setattr(onnx_pack_installer, "_BUNDLE_PYTHON", sys.version_info[:2])
        monkeypatch.setattr(onnx_pack_installer.sys, "platform", "linux")
        monkeypatch.setattr(onnx_pack_installer.platform, "machine", lambda: "x86_64")
        spec = dataclasses.replace(
            onnx_pack_installer._WHEELS[("linux", "x86_64")],
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        wheels = dict(onnx_pack_installer._WHEELS)
        wheels[("linux", "x86_64")] = spec
        monkeypatch.setattr(onnx_pack_installer, "_WHEELS", wheels)
        _patch_download(monkeypatch, spec, payload=payload)
        with pytest.raises(SetupError):
            onnx_pack_installer.install_onnx_pack(tmp_path)

    def test_wheel_without_package_raises(self, tmp_path, monkeypatch):
        payload = _make_wheel({"otherpkg/__init__.py": b"x"})
        monkeypatch.setattr(onnx_pack_installer, "_BUNDLE_PYTHON", sys.version_info[:2])
        monkeypatch.setattr(onnx_pack_installer.sys, "platform", "linux")
        monkeypatch.setattr(onnx_pack_installer.platform, "machine", lambda: "x86_64")
        spec = dataclasses.replace(
            onnx_pack_installer._WHEELS[("linux", "x86_64")],
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        wheels = dict(onnx_pack_installer._WHEELS)
        wheels[("linux", "x86_64")] = spec
        monkeypatch.setattr(onnx_pack_installer, "_WHEELS", wheels)
        _patch_download(monkeypatch, spec, payload=payload)
        with pytest.raises(SetupError, match="onnxruntime/"):
            onnx_pack_installer.install_onnx_pack(tmp_path)

    def test_path_traversal_member_rejected(self, tmp_path, monkeypatch):
        # A malicious member escaping the staging dir must be rejected.
        payload = _make_wheel(
            {
                "onnxruntime/__init__.py": b"x",
                "onnxruntime/../../evil.py": b"pwned",
            }
        )
        monkeypatch.setattr(onnx_pack_installer, "_BUNDLE_PYTHON", sys.version_info[:2])
        monkeypatch.setattr(onnx_pack_installer.sys, "platform", "linux")
        monkeypatch.setattr(onnx_pack_installer.platform, "machine", lambda: "x86_64")
        spec = dataclasses.replace(
            onnx_pack_installer._WHEELS[("linux", "x86_64")],
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        wheels = dict(onnx_pack_installer._WHEELS)
        wheels[("linux", "x86_64")] = spec
        monkeypatch.setattr(onnx_pack_installer, "_WHEELS", wheels)
        _patch_download(monkeypatch, spec, payload=payload)
        with pytest.raises(SetupError, match="unsafe path"):
            onnx_pack_installer.install_onnx_pack(tmp_path)
        assert not (tmp_path.parent / "evil.py").exists()


class TestCancel:
    def test_cancel_before_download_raises_nothing_promoted(self, tmp_path, monkeypatch):
        spec = _force_supported_linux(monkeypatch)
        _patch_download(monkeypatch, spec)
        ev = threading.Event()
        ev.set()
        with pytest.raises(SetupError, match="cancel"):
            onnx_pack_installer.install_onnx_pack(tmp_path, cancel_event=ev)
        assert not (tmp_path / "onnxruntime").exists()

    def test_cancel_after_download_leaves_no_promoted_dir(self, tmp_path, monkeypatch):
        _force_supported_linux(monkeypatch)
        ev = threading.Event()

        def fake_download(url, *, dest_dir, progress=None, cancelled_check=None, max_bytes=None):
            dest_dir.mkdir(parents=True, exist_ok=True)
            part = dest_dir / "fake.part"
            part.write_bytes(_ORT_WHEEL)
            ev.set()  # trip the post-download cancel check
            return part

        monkeypatch.setattr(onnx_pack_installer, "download_to_temp", fake_download)
        with pytest.raises(SetupError, match="cancel"):
            onnx_pack_installer.install_onnx_pack(tmp_path, cancel_event=ev)
        assert not (tmp_path / "onnxruntime").exists()
        assert list(tmp_path.glob("*.part")) == []


class TestUnsupportedPlatform:
    def test_install_when_unsupported_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(onnx_pack_installer, "_current_spec", lambda: None)
        with pytest.raises(SetupError):
            onnx_pack_installer.install_onnx_pack(tmp_path)


def test_install_returns_path_type(tmp_path, monkeypatch):
    spec = _force_supported_linux(monkeypatch)
    _patch_download(monkeypatch, spec)
    result = onnx_pack_installer.install_onnx_pack(tmp_path)
    assert isinstance(result, Path)
