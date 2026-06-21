"""Tests for the yt-dlp binary runtime resolver."""

import dataclasses

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.utils import ytdlp_resolver
from anki_miner.utils.ytdlp_resolver import (
    resolve_ytdlp,
    ytdlp_binary_name,
    ytdlp_download_dir,
)


@pytest.fixture(autouse=True)
def _clear_resolver_cache():
    """Ensure the module-level cache never leaks across tests."""
    ytdlp_resolver._clear_cache()
    yield
    ytdlp_resolver._clear_cache()


@pytest.fixture
def base_config(tmp_path):
    return AnkiMinerConfig(media_temp_folder=tmp_path / "temp_media")


def _make_executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


class TestBinaryName:
    def test_linux_name(self, monkeypatch):
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "linux")
        assert ytdlp_binary_name() == "yt-dlp"

    def test_macos_name(self, monkeypatch):
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "darwin")
        assert ytdlp_binary_name() == "yt-dlp"

    def test_windows_name(self, monkeypatch):
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "win32")
        assert ytdlp_binary_name() == "yt-dlp.exe"


class TestDownloadDir:
    def test_is_home_bin(self, monkeypatch, tmp_path):
        # ytdlp_download_dir reads ANKI_MINER_HOME at call time.
        monkeypatch.setattr(ytdlp_resolver.paths, "ANKI_MINER_HOME", tmp_path)
        assert ytdlp_download_dir() == tmp_path / "bin"


class TestResolveYtdlp:
    def test_default_returns_bare_literal(self, base_config, monkeypatch):
        # No override, not frozen, no downloaded copy -> bare "yt-dlp".
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", False, raising=False)
        assert resolve_ytdlp(base_config) == "yt-dlp"

    def test_config_override_wins_when_file_exists(self, base_config, tmp_path):
        binary = _make_executable(tmp_path / "my-yt-dlp")
        config = dataclasses.replace(base_config, ytdlp_location=binary)
        assert resolve_ytdlp(config) == str(binary)

    def test_config_override_ignored_when_file_missing(self, base_config, tmp_path, monkeypatch):
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", False, raising=False)
        config = dataclasses.replace(base_config, ytdlp_location=tmp_path / "does-not-exist")
        assert resolve_ytdlp(config) == "yt-dlp"

    def test_downloaded_copy_used(self, base_config, tmp_path, monkeypatch):
        bin_dir = tmp_path / "home" / "bin"
        downloaded = _make_executable(bin_dir / "yt-dlp")
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", False, raising=False)
        monkeypatch.setattr(ytdlp_resolver, "ytdlp_download_dir", lambda: bin_dir)
        assert resolve_ytdlp(base_config) == str(downloaded)

    def test_downloaded_non_exec_falls_through(self, base_config, tmp_path, monkeypatch):
        bin_dir = tmp_path / "home" / "bin"
        bin_dir.mkdir(parents=True)
        downloaded = bin_dir / "yt-dlp"
        downloaded.write_text("#!/bin/sh\n")
        downloaded.chmod(0o644)
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", False, raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "linux")
        monkeypatch.setattr(ytdlp_resolver, "ytdlp_download_dir", lambda: bin_dir)
        assert resolve_ytdlp(base_config) == "yt-dlp"

    def test_bundled_used_when_frozen(self, base_config, tmp_path, monkeypatch):
        bundled = _make_executable(tmp_path / "bin" / "yt-dlp")
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", True, raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "linux")
        # No downloaded copy in the (isolated) home.
        assert resolve_ytdlp(base_config) == str(bundled)

    def test_bundled_non_executable_falls_through(self, base_config, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        bundled = bin_dir / "yt-dlp"
        bundled.write_text("#!/bin/sh\n")
        bundled.chmod(0o644)
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", True, raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "linux")
        assert resolve_ytdlp(base_config) == "yt-dlp"

    def test_bundled_windows_exe_name(self, base_config, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        bundled = bin_dir / "yt-dlp.exe"
        bundled.write_text("binary")
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", True, raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "win32")
        assert resolve_ytdlp(base_config) == str(bundled)

    def test_downloaded_beats_bundled(self, base_config, tmp_path, monkeypatch):
        download_dir = tmp_path / "home" / "bin"
        downloaded = _make_executable(download_dir / "yt-dlp")
        bundled = _make_executable(tmp_path / "bin" / "yt-dlp")
        assert bundled.exists()
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", True, raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "linux")
        monkeypatch.setattr(ytdlp_resolver, "ytdlp_download_dir", lambda: download_dir)
        assert resolve_ytdlp(base_config) == str(downloaded)

    def test_override_beats_downloaded(self, base_config, tmp_path, monkeypatch):
        override = _make_executable(tmp_path / "override-yt-dlp")
        download_dir = tmp_path / "home" / "bin"
        _make_executable(download_dir / "yt-dlp")
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", False, raising=False)
        monkeypatch.setattr(ytdlp_resolver, "ytdlp_download_dir", lambda: download_dir)
        config = dataclasses.replace(base_config, ytdlp_location=override)
        assert resolve_ytdlp(config) == str(override)


class TestCaching:
    def test_cache_cleared_unmasks_fresh_download(self, base_config, tmp_path, monkeypatch):
        # First call: no download present -> bare literal.
        download_dir = tmp_path / "home" / "bin"
        monkeypatch.setattr(ytdlp_resolver.sys, "frozen", False, raising=False)
        monkeypatch.setattr(ytdlp_resolver.sys, "platform", "linux")
        monkeypatch.setattr(ytdlp_resolver, "ytdlp_download_dir", lambda: download_dir)
        assert resolve_ytdlp(base_config) == "yt-dlp"

        # A download appears after startup; without _clear_cache the stale
        # literal would be returned. The updater calls _clear_cache() post-install.
        downloaded = _make_executable(download_dir / "yt-dlp")
        ytdlp_resolver._clear_cache()
        assert resolve_ytdlp(base_config) == str(downloaded)

    def test_cache_does_not_mask_changed_override(self, base_config, tmp_path):
        first = _make_executable(tmp_path / "yt-dlp-a")
        second = _make_executable(tmp_path / "yt-dlp-b")
        cfg_a = dataclasses.replace(base_config, ytdlp_location=first)
        cfg_b = dataclasses.replace(base_config, ytdlp_location=second)
        assert resolve_ytdlp(cfg_a) == str(first)
        assert resolve_ytdlp(cfg_b) == str(second)
