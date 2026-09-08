"""Tests for the mokuro installer: uv download/verify/place, venv, pip install, cancel."""

from __future__ import annotations

import hashlib
import io
import tarfile
import threading
import zipfile
from pathlib import Path

import pytest

from anki_miner.exceptions import OperationCancelled, SetupError
from anki_miner.services import mokuro_installer as mi
from anki_miner.utils.process_supervisor import SupervisedResult, SupervisedState


def _tar_with_uv(tmp_path: Path, member_dir: str) -> tuple[Path, str]:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"#!/bin/sh\necho uv\n"
        info = tarfile.TarInfo(f"{member_dir}/uv")
        info.size = len(data)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(data))
    archive = tmp_path / "uv.tar.gz"
    archive.write_bytes(buf.getvalue())
    return archive, hashlib.sha256(buf.getvalue()).hexdigest()


def _zip_with_uv(tmp_path: Path) -> tuple[Path, str]:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("uv.exe", b"MZ")
    archive = tmp_path / "uv.zip"
    archive.write_bytes(buf.getvalue())
    return archive, hashlib.sha256(buf.getvalue()).hexdigest()


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(mi.sys, "platform", "linux")
    monkeypatch.setattr(mi.platform, "machine", lambda: "x86_64")


def _spec_for(archive: Path, sha: str, *, kind: str, member: str) -> mi._UvSpec:
    return mi._UvSpec(
        url=f"https://github.com/astral-sh/uv/releases/download/{mi.UV_VERSION}/{archive.name}",
        sha256=sha,
        archive_kind=kind,
        member=member,
    )


def _ok_run(calls: list[list[str]]):
    def fake(cmd, **kwargs):
        calls.append([str(c) for c in cmd])
        cb = kwargs.get("line_callback")
        if cb and "pip" in calls[-1]:
            cb("Resolved 46 packages in 2.31s")
            cb("Installed 46 packages in 1.20s")
        return SupervisedResult(SupervisedState.COMPLETED, 0, "", "")

    return fake


def test_supported_on_known_targets(monkeypatch):
    for plat, machine in (
        ("linux", "x86_64"),
        ("linux", "aarch64"),
        ("darwin", "arm64"),
        ("darwin", "x86_64"),
        ("win32", "AMD64"),
    ):
        monkeypatch.setattr(mi.sys, "platform", plat)
        monkeypatch.setattr(mi.platform, "machine", lambda m=machine: m)
        assert mi.mokuro_install_supported(), (plat, machine)
    monkeypatch.setattr(mi.sys, "platform", "linux")
    monkeypatch.setattr(mi.platform, "machine", lambda: "riscv64")
    assert not mi.mokuro_install_supported()


def test_every_pinned_spec_is_well_formed():
    for spec in mi._UV_SPECS.values():
        assert spec.url.startswith(f"https://github.com/astral-sh/uv/releases/download/{mi.UV_VERSION}/uv-")
        assert len(spec.sha256) == 64 and spec.archive_kind in ("tar.gz", "zip")


def test_full_install_places_uv_then_runs_venv_and_pip(linux, tmp_path, monkeypatch):
    archive, sha = _tar_with_uv(tmp_path, "uv-x86_64-unknown-linux-gnu")
    monkeypatch.setattr(
        mi, "_current_spec", lambda: _spec_for(archive, sha, kind="tar.gz", member="uv-x86_64-unknown-linux-gnu/uv")
    )
    monkeypatch.setattr(mi, "download_to_temp", lambda url, **kw: archive)
    calls: list[list[str]] = []
    monkeypatch.setattr(mi, "run_supervised", _ok_run(calls))
    bin_root, uv_root = tmp_path / "bin", tmp_path / "uv"
    shim = uv_root / "mokuro" / "bin" / "mokuro"

    def _write_shim(cmd, **kwargs):
        result = _ok_run(calls)(cmd, **kwargs)
        if "pip" in [str(c) for c in cmd]:
            shim.parent.mkdir(parents=True, exist_ok=True)
            shim.write_text("#!/x\n")
            shim.chmod(0o755)
        return result

    monkeypatch.setattr(mi, "run_supervised", _write_shim)
    statuses: list[str] = []
    out = mi.install_mokuro(bin_root, uv_root, status=statuses.append)

    assert out == shim
    uv = bin_root / "uv"
    assert uv.is_file() and uv.stat().st_mode & 0o111
    assert (bin_root / "uv.version").read_text().strip() == mi.UV_VERSION
    venv_cmd, pip_cmd = calls
    assert venv_cmd == [str(uv), "venv", "--python", mi.MOKURO_PYTHON, "--clear", str(uv_root / "mokuro")]
    assert pip_cmd == [
        str(uv),
        "pip",
        "install",
        "--python",
        str(uv_root / "mokuro" / "bin" / "python"),
        "--torch-backend",
        "auto",
        mi.MOKURO_REQUIREMENT,
    ]
    assert statuses == [
        mi.STATUS_DOWNLOADING_UV,
        mi.STATUS_PREPARING_PYTHON,
        mi.STATUS_INSTALLING_MOKURO,
        "Resolved 46 packages in 2.31s",
        mi.STATUS_DOWNLOADING_PACKAGES,
        "Installed 46 packages in 1.20s",
    ]


def test_uv_env_is_self_contained(linux, tmp_path):
    env = mi._uv_env(tmp_path / "uv")
    assert env["UV_PYTHON_INSTALL_DIR"] == str(tmp_path / "uv" / "python")
    assert env["UV_PYTHON_PREFERENCE"] == "only-managed"
    assert env["UV_NO_CACHE"] == "1" and env["UV_NO_CONFIG"] == "1" and env["UV_NO_PROGRESS"] == "1"
    assert "VIRTUAL_ENV" not in env and "CONDA_PREFIX" not in env


def test_windows_zip_member_and_exe_name(tmp_path, monkeypatch):
    monkeypatch.setattr(mi.sys, "platform", "win32")
    monkeypatch.setattr(mi.platform, "machine", lambda: "AMD64")
    archive, sha = _zip_with_uv(tmp_path)
    spec = _spec_for(archive, sha, kind="zip", member="uv.exe")
    (tmp_path / "bin").mkdir()
    target = mi._place_uv(archive, spec, tmp_path / "bin")
    assert target == tmp_path / "bin" / "uv.exe" and target.read_bytes() == b"MZ"


def test_sha_mismatch_refuses(linux, tmp_path, monkeypatch):
    archive, _sha = _tar_with_uv(tmp_path, "uv-x86_64-unknown-linux-gnu")
    monkeypatch.setattr(
        mi,
        "_current_spec",
        lambda: _spec_for(archive, "0" * 64, kind="tar.gz", member="uv-x86_64-unknown-linux-gnu/uv"),
    )
    monkeypatch.setattr(mi, "download_to_temp", lambda url, **kw: archive)
    with pytest.raises(SetupError):
        mi.install_mokuro(tmp_path / "bin", tmp_path / "uv")
    assert not (tmp_path / "bin" / "uv").exists()


def test_existing_uv_with_matching_receipt_is_not_redownloaded(linux, tmp_path, monkeypatch):
    bin_root = tmp_path / "bin"
    bin_root.mkdir()
    (bin_root / "uv").write_text("#!/x\n")
    (bin_root / "uv").chmod(0o755)
    (bin_root / "uv.version").write_text(mi.UV_VERSION)
    monkeypatch.setattr(mi, "download_to_temp", lambda *a, **k: pytest.fail("must not download"))
    calls: list[list[str]] = []
    shim = tmp_path / "uv" / "mokuro" / "bin" / "mokuro"

    def fake(cmd, **kwargs):
        calls.append([str(c) for c in cmd])
        shim.parent.mkdir(parents=True, exist_ok=True)
        shim.write_text("x")
        shim.chmod(0o755)
        return SupervisedResult(SupervisedState.COMPLETED, 0, "", "")

    monkeypatch.setattr(mi, "run_supervised", fake)
    mi.install_mokuro(bin_root, tmp_path / "uv")
    assert len(calls) == 2


def test_pip_failure_raises_with_tail(linux, tmp_path, monkeypatch):
    bin_root = tmp_path / "bin"
    bin_root.mkdir()
    (bin_root / "uv").write_text("#!/x\n")
    (bin_root / "uv").chmod(0o755)
    (bin_root / "uv.version").write_text(mi.UV_VERSION)

    def fake(cmd, **kwargs):
        if "pip" in [str(c) for c in cmd]:
            kwargs["line_callback"]("error: No solution found when resolving dependencies")
            return SupervisedResult(SupervisedState.FAILED, 1, "", "")
        return SupervisedResult(SupervisedState.COMPLETED, 0, "", "")

    monkeypatch.setattr(mi, "run_supervised", fake)
    with pytest.raises(SetupError, match="No solution found"):
        mi.install_mokuro(bin_root, tmp_path / "uv")


def test_cancel_before_start_raises_operation_cancelled(linux, tmp_path, monkeypatch):
    ev = threading.Event()
    ev.set()
    monkeypatch.setattr(mi, "download_to_temp", lambda *a, **k: pytest.fail("must not download"))
    with pytest.raises(OperationCancelled):
        mi.install_mokuro(tmp_path / "bin", tmp_path / "uv", cancel_event=ev)


def test_supervised_cancel_raises_operation_cancelled(linux, tmp_path, monkeypatch):
    bin_root = tmp_path / "bin"
    bin_root.mkdir()
    (bin_root / "uv").write_text("#!/x\n")
    (bin_root / "uv").chmod(0o755)
    (bin_root / "uv.version").write_text(mi.UV_VERSION)
    monkeypatch.setattr(mi, "run_supervised", lambda *a, **k: SupervisedResult(SupervisedState.CANCELLED, None, "", ""))
    with pytest.raises(OperationCancelled):
        mi.install_mokuro(bin_root, tmp_path / "uv")


def test_is_installed(linux, tmp_path):
    assert mi.is_installed(tmp_path / "uv") is False
    shim = tmp_path / "uv" / "mokuro" / "bin" / "mokuro"
    shim.parent.mkdir(parents=True)
    shim.write_text("x")
    shim.chmod(0o755)
    assert mi.is_installed(tmp_path / "uv") is True
