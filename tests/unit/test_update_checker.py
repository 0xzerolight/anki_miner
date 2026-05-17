"""Tests for update_checker module."""

import json
from unittest.mock import MagicMock, patch

from anki_miner.services.update_checker import (
    UpdateChecker,
    UpdateInfo,
    _detect_target,
    _pick_asset,
)

# ---------------------------------------------------------------------------
# TestIsNewer
# ---------------------------------------------------------------------------


class TestIsNewer:
    """Tests for UpdateChecker._is_newer version comparison via packaging.Version."""

    def test_newer_major(self):
        """Should detect newer major version."""
        assert UpdateChecker._is_newer("3.0.0", "2.0.4") is True

    def test_newer_minor(self):
        """Should detect newer minor version."""
        assert UpdateChecker._is_newer("2.1.0", "2.0.4") is True

    def test_newer_patch(self):
        """Should detect newer patch version."""
        assert UpdateChecker._is_newer("2.0.5", "2.0.4") is True

    def test_same_version(self):
        """Should return False for same version."""
        assert UpdateChecker._is_newer("2.0.4", "2.0.4") is False

    def test_older_version(self):
        """Should return False for older version."""
        assert UpdateChecker._is_newer("2.0.3", "2.0.4") is False

    def test_older_major(self):
        """Should return False for older major version."""
        assert UpdateChecker._is_newer("1.9.9", "2.0.0") is False

    def test_invalid_latest(self):
        """Should return False for invalid latest version."""
        assert UpdateChecker._is_newer("abc", "2.0.4") is False

    def test_invalid_current(self):
        """Should return False for invalid current version."""
        assert UpdateChecker._is_newer("2.0.5", "abc") is False

    def test_empty_strings(self):
        """Should return False for empty strings."""
        assert UpdateChecker._is_newer("", "") is False

    def test_two_part_versions(self):
        """Should handle two-part version strings."""
        assert UpdateChecker._is_newer("2.1", "2.0") is True

    def test_prerelease_newer_than_release(self):
        """2.4.0-rc1 should be considered newer than 2.3.2."""
        assert UpdateChecker._is_newer("2.4.0-rc1", "2.3.2") is True

    def test_release_not_older_than_own_prerelease(self):
        """2.3.2 should NOT be newer than 2.4.0-rc1."""
        assert UpdateChecker._is_newer("2.3.2", "2.4.0-rc1") is False

    def test_post_release_newer(self):
        """2.3.5.post1 should be newer than 2.3.5."""
        assert UpdateChecker._is_newer("2.3.5.post1", "2.3.5") is True


# ---------------------------------------------------------------------------
# TestDetectTarget
# ---------------------------------------------------------------------------


class TestDetectTarget:
    """Tests for the module-level _detect_target() helper."""

    def test_appimage_takes_precedence_over_frozen(self, monkeypatch):
        """APPIMAGE env var must win even when sys.frozen is set."""
        monkeypatch.setenv("APPIMAGE", "/tmp/AnkiMiner-2.4.0-x86_64.AppImage")
        monkeypatch.setattr("anki_miner.services.update_checker.sys.frozen", True, raising=False)
        monkeypatch.setattr("anki_miner.services.update_checker.sys.platform", "linux")
        assert _detect_target() == "appimage"

    def test_windows_frozen(self, monkeypatch):
        """sys.frozen + win32 → windows-frozen."""
        monkeypatch.delenv("APPIMAGE", raising=False)
        monkeypatch.setattr("anki_miner.services.update_checker.sys.frozen", True, raising=False)
        monkeypatch.setattr("anki_miner.services.update_checker.sys.platform", "win32")
        assert _detect_target() == "windows-frozen"

    def test_macos_frozen(self, monkeypatch):
        """sys.frozen + darwin → macos-frozen."""
        monkeypatch.delenv("APPIMAGE", raising=False)
        monkeypatch.setattr("anki_miner.services.update_checker.sys.frozen", True, raising=False)
        monkeypatch.setattr("anki_miner.services.update_checker.sys.platform", "darwin")
        assert _detect_target() == "macos-frozen"

    def test_linux_frozen(self, monkeypatch):
        """sys.frozen + linux + no APPIMAGE → linux-frozen."""
        monkeypatch.delenv("APPIMAGE", raising=False)
        monkeypatch.setattr("anki_miner.services.update_checker.sys.frozen", True, raising=False)
        monkeypatch.setattr("anki_miner.services.update_checker.sys.platform", "linux")
        assert _detect_target() == "linux-frozen"

    def test_pip_install(self, monkeypatch):
        """No APPIMAGE, no sys.frozen → pip."""
        monkeypatch.delenv("APPIMAGE", raising=False)
        monkeypatch.setattr("anki_miner.services.update_checker.sys.frozen", False, raising=False)
        monkeypatch.setattr("anki_miner.services.update_checker.sys.platform", "linux")
        assert _detect_target() == "pip"


# ---------------------------------------------------------------------------
# TestPickAsset
# ---------------------------------------------------------------------------


def _make_assets() -> list[dict]:
    """Realistic GitHub /releases/latest assets array (subset of fields)."""
    base = "https://github.com/0xzerolight/anki_miner/releases/download/v2.4.0/"
    return [
        {
            "name": "AnkiMiner-2.4.0-Windows-x86_64-Setup.exe",
            "browser_download_url": f"{base}AnkiMiner-2.4.0-Windows-x86_64-Setup.exe",
        },
        {
            "name": "AnkiMiner-Windows-x86_64.zip",
            "browser_download_url": f"{base}AnkiMiner-Windows-x86_64.zip",
        },
        {
            "name": "anki-miner_2.4.0_amd64.deb",
            "browser_download_url": f"{base}anki-miner_2.4.0_amd64.deb",
        },
        {
            "name": "AnkiMiner-Linux-x86_64.tar.gz",
            "browser_download_url": f"{base}AnkiMiner-Linux-x86_64.tar.gz",
        },
        {
            "name": "AnkiMiner-2.4.0-x86_64.AppImage",
            "browser_download_url": f"{base}AnkiMiner-2.4.0-x86_64.AppImage",
        },
        {
            "name": "AnkiMiner-macOS-arm64.tar.gz",
            "browser_download_url": f"{base}AnkiMiner-macOS-arm64.tar.gz",
        },
    ]


class TestPickAsset:
    """Tests for _pick_asset() per target × asset combo."""

    def test_windows_frozen_picks_setup_exe(self):
        url = _pick_asset(_make_assets(), "windows-frozen")
        assert url is not None
        assert url.endswith("AnkiMiner-2.4.0-Windows-x86_64-Setup.exe")

    def test_linux_frozen_prefers_deb(self):
        url = _pick_asset(_make_assets(), "linux-frozen")
        assert url is not None
        assert url.endswith("anki-miner_2.4.0_amd64.deb")

    def test_linux_frozen_falls_back_to_tar_gz(self):
        """When no .deb is published, fall back to the Linux tar.gz."""
        assets = [a for a in _make_assets() if not a["name"].endswith(".deb")]
        url = _pick_asset(assets, "linux-frozen")
        assert url is not None
        assert url.endswith("AnkiMiner-Linux-x86_64.tar.gz")

    def test_appimage_picks_appimage(self):
        url = _pick_asset(_make_assets(), "appimage")
        assert url is not None
        assert url.endswith(".AppImage")

    def test_macos_frozen_picks_arm64_tar_gz(self):
        url = _pick_asset(_make_assets(), "macos-frozen")
        assert url is not None
        assert url.endswith("AnkiMiner-macOS-arm64.tar.gz")

    def test_pip_target_returns_none(self):
        assert _pick_asset(_make_assets(), "pip") is None

    def test_unknown_target_returns_none(self):
        assert _pick_asset(_make_assets(), "weird-target") is None

    def test_empty_assets_returns_none(self):
        assert _pick_asset([], "linux-frozen") is None

    def test_asset_without_url_is_skipped(self):
        """Asset entries missing browser_download_url are ignored."""
        assets = [{"name": "anki-miner_2.4.0_amd64.deb"}]
        assert _pick_asset(assets, "linux-frozen") is None


# ---------------------------------------------------------------------------
# TestCheckForUpdate
# ---------------------------------------------------------------------------


class TestCheckForUpdate:
    """Tests for UpdateChecker.check_for_update."""

    def _make_response(
        self,
        tag_name: str,
        html_url: str,
        body: str = "release notes",
        assets: list[dict] | None = None,
    ) -> bytes:
        """Create a mock GitHub API response body."""
        payload: dict = {
            "tag_name": tag_name,
            "html_url": html_url,
            "body": body,
            "assets": assets if assets is not None else _make_assets(),
        }
        return json.dumps(payload).encode("utf-8")

    def _mock_urlopen(self, mock, body: bytes) -> MagicMock:
        """Wire up a context-manager-style urlopen mock."""
        response = MagicMock()
        response.read.return_value = body
        response.__enter__ = lambda s: s
        response.__exit__ = MagicMock(return_value=False)
        mock.return_value = response
        return response

    @patch("anki_miner.services.update_checker.urllib.request.urlopen")
    def test_update_available_returns_update_info(self, mock_urlopen, monkeypatch):
        """Returns UpdateInfo with version + asset_url when update is available."""
        # Force linux-frozen so we get a deterministic asset match.
        monkeypatch.delenv("APPIMAGE", raising=False)
        monkeypatch.setattr("anki_miner.services.update_checker.sys.frozen", True, raising=False)
        monkeypatch.setattr("anki_miner.services.update_checker.sys.platform", "linux")

        self._mock_urlopen(
            mock_urlopen,
            self._make_response("v3.0.0", "https://github.com/0xzerolight/anki_miner/releases/tag/v3.0.0"),
        )

        checker = UpdateChecker("2.0.4")
        result = checker.check_for_update()

        assert isinstance(result, UpdateInfo)
        assert result.version == "3.0.0"
        assert "releases" in result.release_page_url
        assert result.asset_url is not None
        assert result.asset_url.endswith(".deb")
        assert result.release_notes == "release notes"

    @patch("anki_miner.services.update_checker.urllib.request.urlopen")
    def test_no_update_available_returns_none(self, mock_urlopen):
        """Returns None when already up to date."""
        self._mock_urlopen(
            mock_urlopen,
            self._make_response("v2.0.4", "https://github.com/0xzerolight/anki_miner/releases/tag/v2.0.4"),
        )
        checker = UpdateChecker("2.0.4")
        assert checker.check_for_update() is None

    @patch("anki_miner.services.update_checker.urllib.request.urlopen")
    def test_strips_v_prefix(self, mock_urlopen, monkeypatch):
        """Should strip 'v' prefix from tag name."""
        monkeypatch.delenv("APPIMAGE", raising=False)
        monkeypatch.setattr("anki_miner.services.update_checker.sys.frozen", False, raising=False)

        self._mock_urlopen(mock_urlopen, self._make_response("v2.1.0", "https://example.com"))

        checker = UpdateChecker("2.0.4")
        result = checker.check_for_update()

        assert result is not None
        assert result.version == "2.1.0"

    @patch("anki_miner.services.update_checker.urllib.request.urlopen")
    def test_network_error_returns_none(self, mock_urlopen):
        """Should return None on network error."""
        mock_urlopen.side_effect = ConnectionError("No internet")
        assert UpdateChecker("2.0.4").check_for_update() is None

    @patch("anki_miner.services.update_checker.urllib.request.urlopen")
    def test_timeout_returns_none(self, mock_urlopen):
        """Should return None on timeout."""
        from urllib.error import URLError

        mock_urlopen.side_effect = URLError("timed out")
        assert UpdateChecker("2.0.4").check_for_update() is None

    @patch("anki_miner.services.update_checker.urllib.request.urlopen")
    def test_invalid_json_returns_none(self, mock_urlopen):
        """Should return None on invalid JSON response."""
        self._mock_urlopen(mock_urlopen, b"not json")
        assert UpdateChecker("2.0.4").check_for_update() is None

    @patch("anki_miner.services.update_checker.urllib.request.urlopen")
    def test_tag_without_v_prefix(self, mock_urlopen, monkeypatch):
        """Should handle tag names without 'v' prefix."""
        monkeypatch.delenv("APPIMAGE", raising=False)
        monkeypatch.setattr("anki_miner.services.update_checker.sys.frozen", False, raising=False)

        self._mock_urlopen(mock_urlopen, self._make_response("2.1.0", "https://example.com"))

        checker = UpdateChecker("2.0.4")
        result = checker.check_for_update()

        assert result is not None
        assert result.version == "2.1.0"

    @patch("anki_miner.services.update_checker.urllib.request.urlopen")
    def test_pip_target_returns_none_asset_url(self, mock_urlopen, monkeypatch):
        """pip install (no sys.frozen, no APPIMAGE) yields asset_url=None."""
        monkeypatch.delenv("APPIMAGE", raising=False)
        monkeypatch.setattr("anki_miner.services.update_checker.sys.frozen", False, raising=False)

        self._mock_urlopen(mock_urlopen, self._make_response("v2.4.0", "https://example.com"))

        checker = UpdateChecker("2.0.4")
        result = checker.check_for_update()

        assert result is not None
        assert result.asset_url is None
        assert result.release_page_url == "https://example.com"

    @patch("anki_miner.services.update_checker.urllib.request.urlopen")
    def test_user_agent_header_sent(self, mock_urlopen):
        """The request must carry a User-Agent identifying anki-miner."""
        self._mock_urlopen(mock_urlopen, self._make_response("v2.0.4", "https://example.com"))

        UpdateChecker("2.0.4").check_for_update()

        assert mock_urlopen.call_count == 1
        request = mock_urlopen.call_args.args[0]
        # urllib.request.Request lowercases header keys via header_items().
        headers = {k.lower(): v for k, v in request.header_items()}
        assert "user-agent" in headers
        ua = headers["user-agent"]
        assert ua.startswith("anki-miner/")
        assert "github.com/0xzerolight/anki_miner" in ua

    @patch("anki_miner.services.update_checker.urllib.request.urlopen")
    def test_missing_body_field_yields_empty_string(self, mock_urlopen):
        """A null/missing body field is coerced to empty string, not None."""
        body = json.dumps({"tag_name": "v3.0.0", "html_url": "https://example.com", "assets": []}).encode("utf-8")
        self._mock_urlopen(mock_urlopen, body)

        result = UpdateChecker("2.0.4").check_for_update()
        assert result is not None
        assert result.release_notes == ""
