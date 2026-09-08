"""Tests for the mokuro executable resolver (override → managed env → PATH)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.utils import mokuro_resolver


@pytest.fixture(autouse=True)
def _fresh_cache():
    mokuro_resolver._clear_cache()
    yield
    mokuro_resolver._clear_cache()


def _exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def test_managed_layout_posix(monkeypatch, tmp_path):
    monkeypatch.setattr(mokuro_resolver.sys, "platform", "linux")
    assert mokuro_resolver.managed_mokuro_path(tmp_path) == tmp_path / "mokuro" / "bin" / "mokuro"
    assert mokuro_resolver.managed_python_path(tmp_path) == tmp_path / "mokuro" / "bin" / "python"


def test_managed_layout_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(mokuro_resolver.sys, "platform", "win32")
    assert mokuro_resolver.managed_mokuro_path(tmp_path) == tmp_path / "mokuro" / "Scripts" / "mokuro.exe"


def test_override_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(mokuro_resolver.sys, "platform", "linux")
    override = _exe(tmp_path / "custom" / "mokuro")
    _exe(tmp_path / "uv" / "mokuro" / "bin" / "mokuro")
    cfg = SimpleNamespace(mokuro_location=override, uv_root=tmp_path / "uv")
    assert mokuro_resolver.resolve_mokuro(cfg) == str(override)


def test_non_executable_override_falls_through_to_managed(tmp_path, monkeypatch):
    monkeypatch.setattr(mokuro_resolver.sys, "platform", "linux")
    override = tmp_path / "custom" / "mokuro"
    override.parent.mkdir()
    override.write_text("")
    managed = _exe(tmp_path / "uv" / "mokuro" / "bin" / "mokuro")
    cfg = SimpleNamespace(mokuro_location=override, uv_root=tmp_path / "uv")
    assert mokuro_resolver.resolve_mokuro(cfg) == str(managed)


def test_literal_when_nothing_managed(tmp_path, monkeypatch):
    monkeypatch.setattr(mokuro_resolver.sys, "platform", "linux")
    cfg = SimpleNamespace(mokuro_location=None, uv_root=tmp_path / "uv")
    assert mokuro_resolver.resolve_mokuro(cfg) == "mokuro"


def test_available_uses_which_for_literal(tmp_path, monkeypatch):
    monkeypatch.setattr(mokuro_resolver.sys, "platform", "linux")
    monkeypatch.setattr(mokuro_resolver.shutil, "which", lambda name: None)
    assert mokuro_resolver.mokuro_available(None, tmp_path / "uv") is False
    monkeypatch.setattr(mokuro_resolver.shutil, "which", lambda name: "/usr/bin/mokuro")
    mokuro_resolver._clear_cache()
    assert mokuro_resolver.mokuro_available(None, tmp_path / "uv") is True


def test_available_true_for_managed(tmp_path, monkeypatch):
    monkeypatch.setattr(mokuro_resolver.sys, "platform", "linux")
    _exe(tmp_path / "uv" / "mokuro" / "bin" / "mokuro")
    assert mokuro_resolver.mokuro_available(None, tmp_path / "uv") is True


def test_cache_keyed_on_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(mokuro_resolver.sys, "platform", "linux")
    cfg = SimpleNamespace(mokuro_location=None, uv_root=tmp_path / "uv")
    assert mokuro_resolver.resolve_mokuro(cfg) == "mokuro"
    managed = _exe(tmp_path / "uv2" / "mokuro" / "bin" / "mokuro")
    cfg2 = SimpleNamespace(mokuro_location=None, uv_root=tmp_path / "uv2")
    assert mokuro_resolver.resolve_mokuro(cfg2) == str(managed)


def test_scrubbed_python_env_drops_host_python_selection_only(monkeypatch):
    for name in ("VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONHOME", "PYTHONPATH"):
        monkeypatch.setenv(name, "/host")
    monkeypatch.setenv("ANKI_MINER_KEEP_ME", "1")
    env = mokuro_resolver.scrubbed_python_env()
    assert not {"VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONHOME", "PYTHONPATH"} & env.keys()
    assert env["ANKI_MINER_KEEP_ME"] == "1"


def test_managed_mokuro_installed_needs_an_executable_shim(monkeypatch, tmp_path):
    monkeypatch.setattr(mokuro_resolver.sys, "platform", "linux")
    assert mokuro_resolver.managed_mokuro_installed(tmp_path) is False
    shim = tmp_path / "mokuro" / "bin" / "mokuro"
    shim.parent.mkdir(parents=True)
    shim.write_text("x")
    assert mokuro_resolver.managed_mokuro_installed(tmp_path) is False  # not executable
    shim.chmod(0o755)
    assert mokuro_resolver.managed_mokuro_installed(tmp_path) is True
