"""Tests for the alass binary installer service."""

from __future__ import annotations

import hashlib
import io
import os
import zipfile
from pathlib import Path

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services import alass_installer

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

# Bytes used as a fake "linux binary" in the happy-path tests.
_FAKE_LINUX_PAYLOAD = b"\x7fELF fake alass binary payload\n"
_FAKE_LINUX_SHA = hashlib.sha256(_FAKE_LINUX_PAYLOAD).hexdigest()

# Bytes used as the inner alass-cli.exe of a fake Windows zip.
_FAKE_EXE_PAYLOAD = b"MZ fake alass-cli.exe payload\n"


def _make_zip_bytes(member_name: str, payload: bytes) -> bytes:
    """Return the bytes of a zip containing a single member."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(member_name, payload)
    return buf.getvalue()


def _fake_download_writing(payload: bytes):
    """Build a download_to_temp replacement that writes *payload* to a .part file."""

    def _download(url, *, dest_dir, progress=None, cancelled_check=None):
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        if cancelled_check is not None and cancelled_check():
            raise SetupError("Download cancelled")
        tmp = dest_dir / "download.part"
        tmp.write_bytes(payload)
        return tmp

    return _download


# ---------------------------------------------------------------------------
# Platform spec selection
# ---------------------------------------------------------------------------


def test_supported_on_linux(monkeypatch):
    monkeypatch.setattr(alass_installer.sys, "platform", "linux")
    assert alass_installer.alass_install_supported() is True


def test_supported_on_win32(monkeypatch):
    monkeypatch.setattr(alass_installer.sys, "platform", "win32")
    assert alass_installer.alass_install_supported() is True


def test_unsupported_on_darwin(monkeypatch):
    monkeypatch.setattr(alass_installer.sys, "platform", "darwin")
    assert alass_installer.alass_install_supported() is False


def test_target_path_linux(tmp_path, monkeypatch):
    monkeypatch.setattr(alass_installer.sys, "platform", "linux")
    assert alass_installer.alass_target_path(tmp_path) == tmp_path / "alass"


def test_target_path_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(alass_installer.sys, "platform", "win32")
    assert alass_installer.alass_target_path(tmp_path) == tmp_path / "alass.exe"


# ---------------------------------------------------------------------------
# is_installed
# ---------------------------------------------------------------------------


def test_is_installed_false_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(alass_installer.sys, "platform", "linux")
    assert alass_installer.is_installed(tmp_path) is False


def test_is_installed_true_for_executable_file(tmp_path, monkeypatch):
    monkeypatch.setattr(alass_installer.sys, "platform", "linux")
    target = tmp_path / "alass"
    target.write_bytes(b"x")
    target.chmod(0o755)
    assert alass_installer.is_installed(tmp_path) is True


def test_is_installed_false_for_non_executable_on_posix(tmp_path, monkeypatch):
    monkeypatch.setattr(alass_installer.sys, "platform", "linux")
    target = tmp_path / "alass"
    target.write_bytes(b"x")
    target.chmod(0o644)
    assert alass_installer.is_installed(tmp_path) is False


def test_is_installed_false_for_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(alass_installer.sys, "platform", "linux")
    (tmp_path / "alass").mkdir()
    assert alass_installer.is_installed(tmp_path) is False


# ---------------------------------------------------------------------------
# install_alass — unsupported platform
# ---------------------------------------------------------------------------


def test_install_unsupported_platform_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(alass_installer.sys, "platform", "darwin")
    with pytest.raises(SetupError):
        alass_installer.install_alass(tmp_path)
    assert not (tmp_path / "alass").exists()


# ---------------------------------------------------------------------------
# install_alass — linux happy path
# ---------------------------------------------------------------------------


def test_install_linux_happy_path(tmp_path, monkeypatch):
    monkeypatch.setattr(alass_installer.sys, "platform", "linux")
    monkeypatch.setattr(
        alass_installer,
        "_LINUX_SPEC",
        alass_installer._AlassSpec(
            url="https://example/alass-linux64",
            sha256=_FAKE_LINUX_SHA,
            is_zip=False,
            zip_member=None,
            dest_name="alass",
        ),
    )
    monkeypatch.setattr(alass_installer, "download_to_temp", _fake_download_writing(_FAKE_LINUX_PAYLOAD))

    bin_root = tmp_path / "bin"
    result = alass_installer.install_alass(bin_root)

    assert result == bin_root / "alass"
    assert result.is_file()
    assert result.read_bytes() == _FAKE_LINUX_PAYLOAD
    assert os.access(result, os.X_OK)
    # The .part temp file must have been cleaned up.
    assert not list(bin_root.glob("*.part"))


# ---------------------------------------------------------------------------
# install_alass — sha256 mismatch
# ---------------------------------------------------------------------------


def test_install_sha256_mismatch_raises_and_leaves_no_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(alass_installer.sys, "platform", "linux")
    monkeypatch.setattr(
        alass_installer,
        "_LINUX_SPEC",
        alass_installer._AlassSpec(
            url="https://example/alass-linux64",
            sha256="0" * 64,  # wrong
            is_zip=False,
            zip_member=None,
            dest_name="alass",
        ),
    )
    monkeypatch.setattr(alass_installer, "download_to_temp", _fake_download_writing(_FAKE_LINUX_PAYLOAD))

    bin_root = tmp_path / "bin"
    with pytest.raises(SetupError):
        alass_installer.install_alass(bin_root)

    assert not (bin_root / "alass").exists()
    assert not list(bin_root.glob("*.part"))


# ---------------------------------------------------------------------------
# install_alass — windows zip path
# ---------------------------------------------------------------------------


def test_install_windows_extracts_cli_exe(tmp_path, monkeypatch):
    monkeypatch.setattr(alass_installer.sys, "platform", "win32")
    zip_bytes = _make_zip_bytes("alass-cli.exe", _FAKE_EXE_PAYLOAD)
    zip_sha = hashlib.sha256(zip_bytes).hexdigest()
    monkeypatch.setattr(
        alass_installer,
        "_WINDOWS_SPEC",
        alass_installer._AlassSpec(
            url="https://example/alass-windows64.zip",
            sha256=zip_sha,
            is_zip=True,
            zip_member="alass-cli.exe",
            dest_name="alass.exe",
        ),
    )
    monkeypatch.setattr(alass_installer, "download_to_temp", _fake_download_writing(zip_bytes))

    bin_root = tmp_path / "bin"
    result = alass_installer.install_alass(bin_root)

    assert result == bin_root / "alass.exe"
    assert result.is_file()
    assert result.read_bytes() == _FAKE_EXE_PAYLOAD
    assert not list(bin_root.glob("*.part"))


def test_install_windows_missing_zip_member_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(alass_installer.sys, "platform", "win32")
    zip_bytes = _make_zip_bytes("something-else.exe", _FAKE_EXE_PAYLOAD)
    zip_sha = hashlib.sha256(zip_bytes).hexdigest()
    monkeypatch.setattr(
        alass_installer,
        "_WINDOWS_SPEC",
        alass_installer._AlassSpec(
            url="https://example/alass-windows64.zip",
            sha256=zip_sha,
            is_zip=True,
            zip_member="alass-cli.exe",
            dest_name="alass.exe",
        ),
    )
    monkeypatch.setattr(alass_installer, "download_to_temp", _fake_download_writing(zip_bytes))

    bin_root = tmp_path / "bin"
    with pytest.raises(SetupError):
        alass_installer.install_alass(bin_root)

    assert not (bin_root / "alass.exe").exists()
    assert not list(bin_root.glob("*.part"))


def test_windows_spec_member_matches_real_archive_layout(tmp_path):
    # Binds the *shipped* _WINDOWS_SPEC.zip_member to the real v2.0.0
    # alass-windows64.zip layout (binary nested under alass-windows64/bin/).
    # The two tests above monkeypatch their own spec, so they never catch a bad
    # module pin; a fat-fingered re-pin must re-open the KeyError in CI, not on a
    # user's machine.
    member = alass_installer._WINDOWS_SPEC.zip_member
    assert member == "alass-windows64/bin/alass-cli.exe"

    # _place_zip_member resolves that exact nested member using the real,
    # un-monkeypatched spec (sha256 is verified upstream in install_alass, not
    # here, so no checksum override is needed at this layer).
    part = tmp_path / "download.part"
    part.write_bytes(_make_zip_bytes(member, _FAKE_EXE_PAYLOAD))
    target = tmp_path / "bin" / "alass.exe"
    target.parent.mkdir(parents=True, exist_ok=True)

    alass_installer._place_zip_member(part, alass_installer._WINDOWS_SPEC, target)

    assert target.is_file()
    assert target.read_bytes() == _FAKE_EXE_PAYLOAD


# ---------------------------------------------------------------------------
# install_alass — cancel before download
# ---------------------------------------------------------------------------


def test_install_cancel_before_download_leaves_bin_clean(tmp_path, monkeypatch):
    import threading

    monkeypatch.setattr(alass_installer.sys, "platform", "linux")
    monkeypatch.setattr(
        alass_installer,
        "_LINUX_SPEC",
        alass_installer._AlassSpec(
            url="https://example/alass-linux64",
            sha256=_FAKE_LINUX_SHA,
            is_zip=False,
            zip_member=None,
            dest_name="alass",
        ),
    )

    calls: list[str] = []

    def _never_download(url, *, dest_dir, progress=None, cancelled_check=None):
        calls.append(url)
        # resource_downloader would raise on a set cancel flag; mimic that.
        if cancelled_check is not None and cancelled_check():
            raise SetupError("Download cancelled")
        return Path(dest_dir) / "download.part"

    monkeypatch.setattr(alass_installer, "download_to_temp", _never_download)

    cancel_event = threading.Event()
    cancel_event.set()

    bin_root = tmp_path / "bin"
    with pytest.raises(SetupError):
        alass_installer.install_alass(bin_root, cancel_event=cancel_event)

    assert not (bin_root / "alass").exists()
    assert not list(bin_root.glob("*.part")) if bin_root.exists() else True
