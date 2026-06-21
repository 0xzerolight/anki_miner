"""Tests for the yt-dlp auto-download / self-update service.

All network + subprocess access is mocked; nothing touches the real network or
the real ~/.anki_miner (home isolation fixtures redirect it to a tmp dir).
"""

import io
import json
import os
import time

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.services import ytdlp_updater
from anki_miner.services.ytdlp_updater import YtdlpUpdater, YtdlpUpdateResult


@pytest.fixture(autouse=True)
def _clear_resolver_cache():
    from anki_miner.utils import ytdlp_resolver

    ytdlp_resolver._clear_cache()
    yield
    ytdlp_resolver._clear_cache()


@pytest.fixture
def config(tmp_path):
    return AnkiMinerConfig(media_temp_folder=tmp_path / "temp_media")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point the updater's bin dir + throttle file at a tmp home."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setattr(ytdlp_updater.paths, "ANKI_MINER_HOME", h)
    from anki_miner.utils import ytdlp_resolver

    monkeypatch.setattr(ytdlp_resolver.paths, "ANKI_MINER_HOME", h)
    return h


def _releases_json(tag="2024.03.10", asset_names=None):
    if asset_names is None:
        asset_names = ["yt-dlp", "yt-dlp.exe", "yt-dlp_macos"]
    return {
        "tag_name": tag,
        "html_url": "https://github.com/yt-dlp/yt-dlp/releases/tag/" + tag,
        "assets": [
            {
                "name": name,
                "browser_download_url": f"https://github.com/yt-dlp/yt-dlp/releases/download/{tag}/{name}",
            }
            for name in asset_names
        ],
    }


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def _fake_urlopen_json(payload):
    def _open(request, timeout=None):  # noqa: ARG001
        return _FakeResponse(json.dumps(payload).encode("utf-8"))

    return _open


class TestValidateGithubUrl:
    def test_accepts_github_host(self):
        assert ytdlp_updater._validate_github_url("https://github.com/x/y") is True

    def test_accepts_objects_host(self):
        assert ytdlp_updater._validate_github_url("https://objects.githubusercontent.com/x") is True

    def test_rejects_off_host(self):
        assert ytdlp_updater._validate_github_url("https://evil.example.com/x") is False

    def test_rejects_http_scheme(self):
        assert ytdlp_updater._validate_github_url("http://github.com/x") is False

    def test_rejects_empty(self):
        assert ytdlp_updater._validate_github_url("") is False


class TestLocalVersion:
    def test_parses_version(self, config, home, monkeypatch):
        class _Proc:
            returncode = 0
            stdout = "2024.02.01\n"

        monkeypatch.setattr(ytdlp_updater.subprocess, "run", lambda *a, **k: _Proc())
        updater = YtdlpUpdater(config)
        assert updater.local_version() == "2024.02.01"

    def test_missing_binary_returns_none(self, config, home, monkeypatch):
        def _raise(*a, **k):
            raise FileNotFoundError()

        monkeypatch.setattr(ytdlp_updater.subprocess, "run", _raise)
        updater = YtdlpUpdater(config)
        assert updater.local_version() is None

    def test_timeout_returns_none(self, config, home, monkeypatch):
        import subprocess as _sp

        def _raise(*a, **k):
            raise _sp.TimeoutExpired(cmd="yt-dlp", timeout=15)

        monkeypatch.setattr(ytdlp_updater.subprocess, "run", _raise)
        updater = YtdlpUpdater(config)
        assert updater.local_version() is None


class TestLatestVersionAndAsset:
    def test_picks_per_os_asset(self, config, home, monkeypatch):
        monkeypatch.setattr(ytdlp_updater.sys, "platform", "linux")
        monkeypatch.setattr(ytdlp_updater.urllib.request, "urlopen", _fake_urlopen_json(_releases_json()))
        updater = YtdlpUpdater(config)
        version, url = updater.latest_version_and_asset()
        assert version == "2024.03.10"
        assert url.endswith("/yt-dlp")

    def test_windows_asset(self, config, home, monkeypatch):
        monkeypatch.setattr(ytdlp_updater.sys, "platform", "win32")
        monkeypatch.setattr(ytdlp_updater.urllib.request, "urlopen", _fake_urlopen_json(_releases_json()))
        updater = YtdlpUpdater(config)
        version, url = updater.latest_version_and_asset()
        assert url.endswith("/yt-dlp.exe")

    def test_macos_asset(self, config, home, monkeypatch):
        monkeypatch.setattr(ytdlp_updater.sys, "platform", "darwin")
        monkeypatch.setattr(ytdlp_updater.urllib.request, "urlopen", _fake_urlopen_json(_releases_json()))
        updater = YtdlpUpdater(config)
        version, url = updater.latest_version_and_asset()
        assert url.endswith("/yt-dlp_macos")

    def test_off_host_asset_rejected(self, config, home, monkeypatch):
        monkeypatch.setattr(ytdlp_updater.sys, "platform", "linux")
        payload = {
            "tag_name": "2024.03.10",
            "assets": [{"name": "yt-dlp", "browser_download_url": "https://evil.example.com/yt-dlp"}],
        }
        monkeypatch.setattr(ytdlp_updater.urllib.request, "urlopen", _fake_urlopen_json(payload))
        updater = YtdlpUpdater(config)
        version, url = updater.latest_version_and_asset()
        # Version still parses, but the off-host URL must be dropped.
        assert version == "2024.03.10"
        assert url is None

    def test_network_failure_returns_none_none(self, config, home, monkeypatch):
        def _raise(*a, **k):
            raise OSError("boom")

        monkeypatch.setattr(ytdlp_updater.urllib.request, "urlopen", _raise)
        updater = YtdlpUpdater(config)
        assert updater.latest_version_and_asset() == (None, None)


class TestThrottle:
    def test_throttled_when_recent(self, config, home):
        updater = YtdlpUpdater(config)
        updater._touch_throttle()
        assert updater._throttled() is True

    def test_not_throttled_when_old(self, config, home):
        updater = YtdlpUpdater(config)
        updater._touch_throttle()
        old = time.time() - (25 * 3600)
        os.utime(updater._throttle_path(), (old, old))
        assert updater._throttled() is False

    def test_not_throttled_when_absent(self, config, home):
        updater = YtdlpUpdater(config)
        assert updater._throttled() is False

    def test_touch_throttle_atomic_no_leftover_tmp(self, config, home):
        updater = YtdlpUpdater(config)
        updater._touch_throttle()
        assert updater._throttle_path().exists()
        leftovers = list(home.glob(".ytdlp_update_check*.tmp"))
        assert leftovers == []


class TestCheckAndUpdate:
    def _patch_latest(self, monkeypatch, version, url):
        monkeypatch.setattr(YtdlpUpdater, "latest_version_and_asset", lambda self: (version, url), raising=True)

    def _patch_local(self, monkeypatch, version):
        monkeypatch.setattr(YtdlpUpdater, "local_version", lambda self: version, raising=True)

    def test_skipped_throttle(self, config, home):
        updater = YtdlpUpdater(config)
        updater._touch_throttle()
        result = updater.check_and_update()
        assert result.action == "skipped_throttle"

    def test_force_bypasses_throttle(self, config, home, monkeypatch):
        self._patch_latest(monkeypatch, None, None)
        updater = YtdlpUpdater(config)
        updater._touch_throttle()
        result = updater.check_and_update(force=True)
        assert result.action == "unavailable"

    def test_unavailable_when_no_latest(self, config, home, monkeypatch):
        self._patch_latest(monkeypatch, None, None)
        updater = YtdlpUpdater(config)
        result = updater.check_and_update()
        assert result.action == "unavailable"

    def test_up_to_date(self, config, home, monkeypatch):
        self._patch_latest(monkeypatch, "2024.02.01", "https://github.com/x/yt-dlp")
        self._patch_local(monkeypatch, "2024.02.01")
        updater = YtdlpUpdater(config)
        result = updater.check_and_update()
        assert result.action == "up_to_date"
        assert result.installed_version == "2024.02.01"

    def test_installed_when_newer(self, config, home, monkeypatch):
        self._patch_latest(monkeypatch, "2024.03.10", "https://github.com/x/yt-dlp")
        self._patch_local(monkeypatch, "2024.02.01")
        installed: dict = {}

        def _install(self, url, version):
            dest = self.download_dir() / "yt-dlp"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("binary")
            installed["url"] = url
            installed["version"] = version
            return dest

        monkeypatch.setattr(YtdlpUpdater, "_download_and_install", _install, raising=True)
        cleared: dict = {}
        from anki_miner.utils import ytdlp_resolver

        monkeypatch.setattr(ytdlp_resolver, "_clear_cache", lambda: cleared.setdefault("c", True))
        updater = YtdlpUpdater(config)
        result = updater.check_and_update()
        assert isinstance(result, YtdlpUpdateResult)
        assert result.action == "installed"
        assert result.installed_version == "2024.03.10"
        assert installed["version"] == "2024.03.10"
        assert cleared.get("c") is True

    def test_installed_when_no_local_version(self, config, home, monkeypatch):
        # Fresh install: local_version None -> proceed to install.
        self._patch_latest(monkeypatch, "2024.03.10", "https://github.com/x/yt-dlp")
        self._patch_local(monkeypatch, None)

        def _install(self, url, version):
            dest = self.download_dir() / "yt-dlp"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("binary")
            return dest

        monkeypatch.setattr(YtdlpUpdater, "_download_and_install", _install, raising=True)
        updater = YtdlpUpdater(config)
        result = updater.check_and_update()
        assert result.action == "installed"

    def test_failed_on_install_exception(self, config, home, monkeypatch):
        self._patch_latest(monkeypatch, "2024.03.10", "https://github.com/x/yt-dlp")
        self._patch_local(monkeypatch, "2024.02.01")

        def _boom(self, url, version):
            raise RuntimeError("disk full")

        monkeypatch.setattr(YtdlpUpdater, "_download_and_install", _boom, raising=True)
        updater = YtdlpUpdater(config)
        result = updater.check_and_update()
        assert result.action == "failed"

    def test_throttle_written_before_network(self, config, home, monkeypatch):
        # The throttle file must exist by the time latest_version_and_asset runs.
        seen: dict = {}

        def _latest(self):
            seen["throttle_exists"] = self._throttle_path().exists()
            return (None, None)

        monkeypatch.setattr(YtdlpUpdater, "latest_version_and_asset", _latest, raising=True)
        updater = YtdlpUpdater(config)
        updater.check_and_update()
        assert seen["throttle_exists"] is True

    def test_never_raises_on_throttle_io_error(self, config, home, monkeypatch):
        # Even if touching the throttle fails, check_and_update must not raise.
        monkeypatch.setattr(
            YtdlpUpdater, "_touch_throttle", lambda self: (_ for _ in ()).throw(OSError("ro fs")), raising=True
        )
        self._patch_latest(monkeypatch, None, None)
        updater = YtdlpUpdater(config)
        result = updater.check_and_update()
        assert result.action == "failed"


class TestDownloadAndInstall:
    def _fake_body(self, monkeypatch, data: bytes):
        def _open(request, timeout=None):  # noqa: ARG001
            return _FakeResponse(data)

        monkeypatch.setattr(ytdlp_updater.urllib.request, "urlopen", _open)

    def test_atomic_install_and_chmod(self, config, home, monkeypatch):
        monkeypatch.setattr(ytdlp_updater.sys, "platform", "linux")
        self._fake_body(monkeypatch, b"x" * (2 * 1024 * 1024))
        updater = YtdlpUpdater(config)
        dest = updater._download_and_install("https://github.com/x/yt-dlp", "2024.03.10")
        assert dest.exists()
        assert dest.read_bytes() == b"x" * (2 * 1024 * 1024)
        assert os.access(dest, os.X_OK)
        # No leftover .tmp files.
        assert list(updater.download_dir().glob("*.tmp")) == []

    def test_size_floor_rejects_small(self, config, home, monkeypatch):
        monkeypatch.setattr(ytdlp_updater.sys, "platform", "linux")
        self._fake_body(monkeypatch, b"tiny")
        updater = YtdlpUpdater(config)
        with pytest.raises(ValueError):
            updater._download_and_install("https://github.com/x/yt-dlp", "2024.03.10")
        # Partial/garbage cleaned up; nothing installed.
        assert not (updater.download_dir() / "yt-dlp").exists()
        assert list(updater.download_dir().glob("*.tmp")) == []

    def test_partial_download_cleanup_on_error(self, config, home, monkeypatch):
        monkeypatch.setattr(ytdlp_updater.sys, "platform", "linux")

        class _Broken(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, *a):
                raise OSError("connection reset")

        monkeypatch.setattr(ytdlp_updater.urllib.request, "urlopen", lambda *a, **k: _Broken())
        updater = YtdlpUpdater(config)
        with pytest.raises(OSError):
            updater._download_and_install("https://github.com/x/yt-dlp", "2024.03.10")
        assert list(updater.download_dir().glob("*.tmp")) == []

    def test_cancel_mid_download_cleans_up(self, config, home, monkeypatch):
        monkeypatch.setattr(ytdlp_updater.sys, "platform", "linux")
        self._fake_body(monkeypatch, b"x" * (2 * 1024 * 1024))
        updater = YtdlpUpdater(config, cancel=lambda: True)
        with pytest.raises(RuntimeError):
            updater._download_and_install("https://github.com/x/yt-dlp", "2024.03.10")
        assert not (updater.download_dir() / "yt-dlp").exists()
        assert list(updater.download_dir().glob("*.tmp")) == []
