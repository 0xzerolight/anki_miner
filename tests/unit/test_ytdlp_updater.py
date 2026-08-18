"""Tests for the yt-dlp auto-download / self-update service.

All network + subprocess access is mocked; nothing touches the real network or
the real ~/.anki_miner (home isolation fixtures redirect it to a tmp dir).
"""

import io
import json
import os
import subprocess
import time

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import OperationCancelled
from anki_miner.services import ytdlp_updater
from anki_miner.services.ytdlp_updater import YtdlpUpdater, YtdlpUpdateResult


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


# Derive the per-OS asset name from the production map instead of hardcoding it.
# These tests force sys.platform = "linux", and the linux asset name changed from
# the "yt-dlp" zipapp to the "yt-dlp_linux" standalone build; a literal here made
# every asset URL and manifest body silently disagree with the code under test.
_LINUX_ASSET = ytdlp_updater._ASSET_BY_PLATFORM["linux"]
_ALL_ASSETS = [*ytdlp_updater._ASSET_BY_PLATFORM.values(), "SHA2-256SUMS"]


def _asset_url(asset_name=None, tag="2024.03.10", repo="yt-dlp/yt-dlp"):
    """Canonical release-download URL for *asset_name* (default: this OS's asset)."""
    name = _LINUX_ASSET if asset_name is None else asset_name
    return f"https://github.com/{repo}/releases/download/{tag}/{name}"


def _releases_json(tag="2024.03.10", asset_names=None, repo="yt-dlp/yt-dlp"):
    if asset_names is None:
        asset_names = list(_ALL_ASSETS)
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/{repo}/releases/tag/{tag}",
        "assets": [
            {
                "name": name,
                "browser_download_url": _asset_url(name, tag=tag, repo=repo),
            }
            for name in asset_names
        ],
    }


# The host GitHub actually 302s release downloads to today. Using the *current*
# host as the default is deliberate: the previous default was the retired
# objects.githubusercontent.com, which made every fake redirect land on a host the
# production allowlist happened to still accept — so the suite stayed green for a
# month while real downloads failed on every platform. Back-compat for the old
# host is asserted explicitly in TestValidateGithubUrl instead of by default.
_REDIRECT_HOST_URL = "https://release-assets.githubusercontent.com/yt-dlp-release-asset"


class _FakeResponse(io.BytesIO):
    def __init__(self, body, final_url=_REDIRECT_HOST_URL):
        super().__init__(body)
        self._final_url = final_url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False

    def geturl(self):
        return self._final_url


def _fake_urlopen_json(payload):
    def _open(request, timeout=None):  # noqa: ARG001
        return _FakeResponse(json.dumps(payload).encode("utf-8"))

    return _open


class TestValidateGithubUrl:
    def test_accepts_github_host(self):
        assert ytdlp_updater._validate_github_url("https://github.com/x/y") is True

    def test_accepts_objects_host(self):
        assert ytdlp_updater._validate_github_url("https://objects.githubusercontent.com/x") is True

    def test_accepts_release_assets_host(self):
        """The host GitHub 302s release downloads to today.

        Omitting it is what made every yt-dlp download fail on every platform
        between the feature shipping and this fix.
        """
        assert ytdlp_updater._validate_github_url("https://release-assets.githubusercontent.com/x") is True

    def test_rejects_off_host(self):
        assert ytdlp_updater._validate_github_url("https://evil.example.com/x") is False

    @pytest.mark.parametrize("host", ["raw.githubusercontent.com", "gist.githubusercontent.com"])
    def test_rejects_user_content_subdomains(self, host):
        """The allowlist is an exact host set, never a *.githubusercontent.com suffix.

        raw. and gist. serve arbitrary user-authored bytes at attacker-chosen
        paths, unlike the opaque release-blob hosts. A suffix match would admit
        them, and the SHA2-256SUMS fetch is guarded by this check alone, so the
        hash cannot backstop a widened host set for that leg.
        """
        assert ytdlp_updater._validate_github_url(f"https://{host}/o/r/main/evil") is False

    def test_rejects_http_scheme(self):
        assert ytdlp_updater._validate_github_url("http://github.com/x") is False

    def test_rejects_empty(self):
        assert ytdlp_updater._validate_github_url("") is False


class TestAssetByPlatform:
    """The per-OS asset map must name standalone builds, never the zipapp.

    The bare "yt-dlp" asset is a zipimport archive that runs the system python3
    and carries no curl_cffi, so installing it would make the packaged app depend
    on a host Python it does not ship and would silently lose impersonation.
    """

    def test_no_platform_uses_the_zipapp_asset(self):
        assert "yt-dlp" not in ytdlp_updater._ASSET_BY_PLATFORM.values()

    def test_expected_standalone_assets(self):
        assert ytdlp_updater._ASSET_BY_PLATFORM == {
            "linux": "yt-dlp_linux",
            "win32": "yt-dlp.exe",
            "darwin": "yt-dlp_macos",
        }


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

    def test_version_probe_ignores_user_config(self, config, home, monkeypatch):
        monkeypatch.setattr(ytdlp_updater.ytdlp_resolver, "resolve_ytdlp", lambda config: "yt-dlp")
        captured: list[str] = []

        def _run(command, **kwargs):  # noqa: ARG001
            captured.extend(command)
            return subprocess.CompletedProcess(command, 0, "2024.02.01\n", "")

        monkeypatch.setattr(ytdlp_updater.subprocess, "run", _run)

        assert YtdlpUpdater(config).local_version() == "2024.02.01"
        assert captured[1] == "--ignore-config"


class TestLatestVersionAndAsset:
    def test_picks_per_os_asset(self, config, home, monkeypatch):
        monkeypatch.setattr(ytdlp_updater.sys, "platform", "linux")
        monkeypatch.setattr(ytdlp_updater.urllib.request, "urlopen", _fake_urlopen_json(_releases_json()))
        updater = YtdlpUpdater(config)
        version, url = updater.latest_version_and_asset()
        assert version == "2024.03.10"
        assert url.endswith(f"/{_LINUX_ASSET}")

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
            "assets": [{"name": _LINUX_ASSET, "browser_download_url": f"https://evil.example.com/{_LINUX_ASSET}"}],
        }
        monkeypatch.setattr(ytdlp_updater.urllib.request, "urlopen", _fake_urlopen_json(payload))
        updater = YtdlpUpdater(config)
        version, url = updater.latest_version_and_asset()
        # Version still parses, but the off-host URL must be dropped.
        assert version == "2024.03.10"
        assert url is None

    def test_missing_sums_manifest_rejects_asset(self, config, home, monkeypatch):
        monkeypatch.setattr(ytdlp_updater.sys, "platform", "linux")
        payload = _releases_json(asset_names=[_LINUX_ASSET])
        monkeypatch.setattr(ytdlp_updater.urllib.request, "urlopen", _fake_urlopen_json(payload))
        updater = YtdlpUpdater(config)
        version, url = updater.latest_version_and_asset()
        assert version == "2024.03.10"
        assert url is None

    def test_network_failure_returns_none_none(self, config, home, monkeypatch):
        def _raise(*a, **k):
            raise OSError("boom")

        monkeypatch.setattr(ytdlp_updater.urllib.request, "urlopen", _raise)
        updater = YtdlpUpdater(config)
        assert updater.latest_version_and_asset() == (None, None)


_NIGHTLY_REPO = "yt-dlp/yt-dlp-nightly-builds"
_NIGHTLY_TAG = "2026.08.18.122307"


class TestPrereleaseChannel:
    """config.ytdlp_prerelease=True flips the updater to the nightly-builds repo."""

    def _prerelease_config(self, tmp_path):
        return AnkiMinerConfig(media_temp_folder=tmp_path / "temp_media", ytdlp_prerelease=True)

    def test_stable_config_queries_stable_repo(self, config, home, monkeypatch):
        monkeypatch.setattr(ytdlp_updater.sys, "platform", "linux")
        seen = []

        def _open(request, timeout=None):  # noqa: ARG001
            seen.append(request.full_url)
            return _FakeResponse(json.dumps(_releases_json()).encode("utf-8"))

        monkeypatch.setattr(ytdlp_updater.urllib.request, "urlopen", _open)
        YtdlpUpdater(config).latest_version_and_asset()
        assert seen == ["https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"]

    def test_prerelease_config_queries_nightly_repo(self, tmp_path, home, monkeypatch):
        monkeypatch.setattr(ytdlp_updater.sys, "platform", "linux")
        seen = []
        payload = _releases_json(tag=_NIGHTLY_TAG, repo=_NIGHTLY_REPO)

        def _open(request, timeout=None):  # noqa: ARG001
            seen.append(request.full_url)
            return _FakeResponse(json.dumps(payload).encode("utf-8"))

        monkeypatch.setattr(ytdlp_updater.urllib.request, "urlopen", _open)
        version, url = YtdlpUpdater(self._prerelease_config(tmp_path)).latest_version_and_asset()
        assert seen == [f"https://api.github.com/repos/{_NIGHTLY_REPO}/releases/latest"]
        assert version == _NIGHTLY_TAG
        assert url == _asset_url(tag=_NIGHTLY_TAG, repo=_NIGHTLY_REPO)

    def test_prerelease_rejects_stable_repo_asset_urls(self, tmp_path, home, monkeypatch):
        """A nightly release whose assets point at the stable repo is off-shape: no URL."""
        monkeypatch.setattr(ytdlp_updater.sys, "platform", "linux")
        payload = _releases_json(tag=_NIGHTLY_TAG)  # stable-repo URLs
        monkeypatch.setattr(ytdlp_updater.urllib.request, "urlopen", _fake_urlopen_json(payload))
        version, url = YtdlpUpdater(self._prerelease_config(tmp_path)).latest_version_and_asset()
        assert version == _NIGHTLY_TAG
        assert url is None

    def test_release_tag_round_trip_with_nightly_repo(self):
        url = ytdlp_updater._release_asset_url(_NIGHTLY_TAG, _LINUX_ASSET, repo=_NIGHTLY_REPO)
        assert ytdlp_updater._release_tag_from_asset_url(url, _LINUX_ASSET, repo=_NIGHTLY_REPO) == _NIGHTLY_TAG
        # Cross-repo must NOT round-trip: stable parser rejects nightly URLs.
        assert ytdlp_updater._release_tag_from_asset_url(url, _LINUX_ASSET) is None

    def test_nightly_orders_above_stable_and_below_next_stable(self):
        from anki_miner.utils.version_compare import is_newer

        assert is_newer(_NIGHTLY_TAG, "2026.07.04")
        assert is_newer("2026.09.01", _NIGHTLY_TAG)


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
        updater = YtdlpUpdater(config)
        result = updater.check_and_update()
        assert isinstance(result, YtdlpUpdateResult)
        assert result.action == "installed"
        assert result.installed_version == "2024.03.10"
        assert installed["version"] == "2024.03.10"

    def test_successful_install_clears_capability_caches(self, config, home, monkeypatch):
        import hashlib

        from anki_miner.services import youtube_fetcher

        monkeypatch.setattr(ytdlp_updater.sys, "platform", "linux")
        data = b"x" * (2 * 1024 * 1024)
        manifest = f"{hashlib.sha256(data).hexdigest()}  {_LINUX_ASSET}\n".encode()

        def fake_urlopen(request, timeout=None):  # noqa: ARG001
            if request.full_url.endswith("/SHA2-256SUMS"):
                return _FakeResponse(manifest)
            return _FakeResponse(data)

        monkeypatch.setattr(ytdlp_updater.urllib.request, "urlopen", fake_urlopen)
        help_outputs = iter(
            [
                "old help",
                "old help",
                "--js-runtimes",
                "--remote-components",
            ]
        )

        def fake_help(command, **kwargs):  # noqa: ARG001
            return subprocess.CompletedProcess(command, 0, next(help_outputs), "")

        monkeypatch.setattr(youtube_fetcher.subprocess, "run", fake_help)
        path = str(home / "bin" / "yt-dlp")
        youtube_fetcher._ytdlp_supports_js_runtimes.cache_clear()
        youtube_fetcher._ytdlp_supports_remote_components.cache_clear()
        try:
            assert youtube_fetcher._ytdlp_supports_js_runtimes(path) is False
            assert youtube_fetcher._ytdlp_supports_remote_components(path) is False

            installed = YtdlpUpdater(config)._download_and_install(_asset_url(), "2024.03.10")

            assert installed == home / "bin" / "yt-dlp"
            assert youtube_fetcher._ytdlp_supports_js_runtimes(path) is True
            assert youtube_fetcher._ytdlp_supports_remote_components(path) is True
        finally:
            youtube_fetcher._ytdlp_supports_js_runtimes.cache_clear()
            youtube_fetcher._ytdlp_supports_remote_components.cache_clear()

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
        import hashlib

        manifest = f"{hashlib.sha256(data).hexdigest()}  {_LINUX_ASSET}\n".encode()

        def _open(request, timeout=None):  # noqa: ARG001
            if request.full_url.endswith("/SHA2-256SUMS"):
                return _FakeResponse(manifest)
            return _FakeResponse(data)

        monkeypatch.setattr(ytdlp_updater.urllib.request, "urlopen", _open)

    def test_atomic_install_and_chmod(self, config, home, monkeypatch):
        monkeypatch.setattr(ytdlp_updater.sys, "platform", "linux")
        self._fake_body(monkeypatch, b"x" * (2 * 1024 * 1024))
        updater = YtdlpUpdater(config)
        dest = updater._download_and_install(_asset_url(), "2024.03.10")
        assert dest.exists()
        assert dest.read_bytes() == b"x" * (2 * 1024 * 1024)
        assert os.access(dest, os.X_OK)
        # No leftover .tmp files.
        assert list(updater.download_dir().glob("*.tmp")) == []

    def test_size_floor_rejects_small(self, config, home, monkeypatch):
        monkeypatch.setattr(ytdlp_updater.sys, "platform", "linux")
        self._fake_body(monkeypatch, b"tiny")
        updater = YtdlpUpdater(config)
        # match= is load-bearing: _download_and_install raises ValueError for a
        # refused URL shape and for a manifest miss too, so a bare raises() would
        # keep passing on those and silently stop testing the size floor.
        with pytest.raises(ValueError, match="implausibly small"):
            updater._download_and_install(_asset_url(), "2024.03.10")
        # Partial/garbage cleaned up; nothing installed.
        assert not (updater.download_dir() / "yt-dlp").exists()
        assert list(updater.download_dir().glob("*.tmp")) == []

    @pytest.mark.parametrize(
        "final_url",
        [
            "https://evil.example.com/yt-dlp",
            "https://raw.githubusercontent.com/o/r/main/yt-dlp",
            "http://github.com/yt-dlp/yt-dlp/releases/download/2024.03.10/yt-dlp_linux",
        ],
    )
    def test_off_allowlist_redirect_refused(self, config, home, monkeypatch, final_url):
        """A redirect landing off the allowlist must refuse before anything installs.

        This branch had no coverage at all: every fake response defaulted to an
        allowlisted host, so the reject path was never executed and the retired-CDN
        regression shipped green.
        """
        data = b"x" * (2 * 1024 * 1024)

        def _open(request, timeout=None):  # noqa: ARG001
            return _FakeResponse(data, final_url=final_url)

        monkeypatch.setattr(ytdlp_updater.sys, "platform", "linux")
        monkeypatch.setattr(ytdlp_updater.urllib.request, "urlopen", _open)
        updater = YtdlpUpdater(config)
        with pytest.raises(ValueError, match="off-allowlist URL"):
            updater._download_and_install(_asset_url(), "2024.03.10")
        # Nothing promoted, nothing staged, nothing left behind.
        assert not (updater.download_dir() / "yt-dlp").exists()
        assert not (updater.download_dir() / "yt-dlp.verified").exists()
        assert list(updater.download_dir().glob("*.tmp")) == []

    def test_off_allowlist_redirect_surfaces_as_failed_result(self, config, home, monkeypatch):
        """check_and_update must report the refusal, not raise (never-raises contract)."""

        def _open(request, timeout=None):  # noqa: ARG001
            return _FakeResponse(b"x" * (2 * 1024 * 1024), final_url="https://evil.example.com/yt-dlp")

        monkeypatch.setattr(ytdlp_updater.sys, "platform", "linux")
        monkeypatch.setattr(YtdlpUpdater, "local_version", lambda self: "2024.01.01")
        monkeypatch.setattr(
            YtdlpUpdater,
            "latest_version_and_asset",
            lambda self: ("2024.03.10", _asset_url()),
        )
        monkeypatch.setattr(ytdlp_updater.urllib.request, "urlopen", _open)
        result = YtdlpUpdater(config).check_and_update(force=True)
        assert result.action == "failed"
        assert "off-allowlist URL" in result.message

    def test_zipapp_asset_url_refused(self, config, home, monkeypatch):
        """A URL naming the bare "yt-dlp" zipapp must not install on Linux.

        Guards the _ASSET_BY_PLATFORM pin from the other direction: even a
        correctly-shaped github.com release URL is refused when it names an asset
        this platform does not expect.
        """
        monkeypatch.setattr(ytdlp_updater.sys, "platform", "linux")
        self._fake_body(monkeypatch, b"x" * (2 * 1024 * 1024))
        updater = YtdlpUpdater(config)
        with pytest.raises(ValueError, match="non-release or mismatched"):
            updater._download_and_install(_asset_url("yt-dlp"), "2024.03.10")
        assert not (updater.download_dir() / "yt-dlp").exists()

    def test_partial_download_cleanup_on_error(self, config, home, monkeypatch):
        monkeypatch.setattr(ytdlp_updater.sys, "platform", "linux")

        class _Broken(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, *a):
                raise OSError("connection reset")

            def geturl(self):
                return _REDIRECT_HOST_URL

        monkeypatch.setattr(ytdlp_updater.urllib.request, "urlopen", lambda *a, **k: _Broken())
        updater = YtdlpUpdater(config)
        with pytest.raises(OSError):
            updater._download_and_install(_asset_url(), "2024.03.10")
        assert list(updater.download_dir().glob("*.tmp")) == []

    def test_cancel_mid_download_cleans_up(self, config, home, monkeypatch):
        monkeypatch.setattr(ytdlp_updater.sys, "platform", "linux")
        self._fake_body(monkeypatch, b"x" * (2 * 1024 * 1024))
        updater = YtdlpUpdater(config, cancel=lambda: True)
        # Typed, not a bare RuntimeError: check_and_update's catch-all used to
        # log a traceback and report a bogus failure for a plain user cancel.
        with pytest.raises(OperationCancelled):
            updater._download_and_install(_asset_url(), "2024.03.10")
        assert not (updater.download_dir() / "yt-dlp").exists()
        assert list(updater.download_dir().glob("*.tmp")) == []

    def test_cancel_during_checksum_fetch_prevents_promotion(self, config, home, monkeypatch):
        import hashlib

        monkeypatch.setattr(ytdlp_updater.sys, "platform", "linux")
        data = b"x" * (2 * 1024 * 1024)
        manifest = f"{hashlib.sha256(data).hexdigest()}  {_LINUX_ASSET}\n".encode()
        cancelled = False

        class _ChecksumResponse(_FakeResponse):
            def read(self, size=-1):
                nonlocal cancelled
                cancelled = True
                return super().read(size)

        def _open(request, timeout=None):  # noqa: ARG001
            if request.full_url.endswith("/SHA2-256SUMS"):
                return _ChecksumResponse(manifest)
            return _FakeResponse(data)

        monkeypatch.setattr(ytdlp_updater.urllib.request, "urlopen", _open)
        updater = YtdlpUpdater(config, cancel=lambda: cancelled)

        with pytest.raises(OperationCancelled):
            updater._download_and_install(_asset_url(), "2024.03.10")

        assert not (updater.download_dir() / "yt-dlp").exists()

    def test_receipt_publication_failure_restores_previous_verified_pair(self, config, home, monkeypatch):
        import hashlib

        monkeypatch.setattr(ytdlp_updater.sys, "platform", "linux")
        new_data = b"new" * (1024 * 1024)
        self._fake_body(monkeypatch, new_data)
        updater = YtdlpUpdater(config)
        final = updater.download_dir() / "yt-dlp"
        final.parent.mkdir(parents=True, exist_ok=True)
        old_data = b"old-working-binary"
        old_digest = hashlib.sha256(old_data).hexdigest()
        final.write_bytes(old_data)
        final.chmod(0o755)
        receipt = final.with_name("yt-dlp.verified")
        receipt.write_text(old_digest, encoding="ascii")
        monkeypatch.setattr(
            updater,
            "_write_verification_receipt",
            lambda binary, sha256: (_ for _ in ()).throw(OSError("receipt publication failed")),
        )

        with pytest.raises(OSError, match="receipt publication failed"):
            updater._download_and_install(_asset_url(), "2024.03.10")

        assert final.read_bytes() == old_data
        assert receipt.read_text(encoding="ascii") == old_digest

    def test_receipt_backup_failure_keeps_previous_verified_pair(self, config, home, monkeypatch):
        import hashlib

        monkeypatch.setattr(ytdlp_updater.sys, "platform", "linux")
        new_data = b"new" * (1024 * 1024)
        self._fake_body(monkeypatch, new_data)
        updater = YtdlpUpdater(config)
        final = updater.download_dir() / "yt-dlp"
        final.parent.mkdir(parents=True, exist_ok=True)
        old_data = b"old-working-binary"
        old_digest = hashlib.sha256(old_data).hexdigest()
        final.write_bytes(old_data)
        final.chmod(0o755)
        receipt = final.with_name("yt-dlp.verified")
        receipt.write_text(old_digest, encoding="ascii")
        real_replace = updater._atomic_replace

        def fail_receipt_backup(source, destination):
            if source == receipt and destination.name.endswith(".rollback"):
                raise OSError("receipt backup failed")
            real_replace(source, destination)

        monkeypatch.setattr(updater, "_atomic_replace", fail_receipt_backup)

        with pytest.raises(OSError, match="receipt backup failed"):
            updater._download_and_install(_asset_url(), "2024.03.10")

        assert final.read_bytes() == old_data
        assert receipt.read_text(encoding="ascii") == old_digest
