"""Tests for update_checker module."""

import json
from unittest.mock import MagicMock, patch

from anki_miner.services.update_checker import UpdateChecker

# ---------------------------------------------------------------------------
# TestIsNewer
# ---------------------------------------------------------------------------


class TestIsNewer:
    """Tests for UpdateChecker._is_newer version comparison."""

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


# ---------------------------------------------------------------------------
# TestCheckForUpdate
# ---------------------------------------------------------------------------


class TestCheckForUpdate:
    """Tests for UpdateChecker.check_for_update."""

    def _make_response(self, tag_name: str, html_url: str) -> bytes:
        """Create a mock GitHub API response body."""
        return json.dumps({"tag_name": tag_name, "html_url": html_url}).encode("utf-8")

    @patch("anki_miner.services.update_checker.urllib.request.urlopen")
    def test_update_available(self, mock_urlopen):
        """Should return (True, version, url) when update is available."""
        mock_response = MagicMock()
        mock_response.read.return_value = self._make_response(
            "v3.0.0", "https://github.com/0xzerolight/anki_miner/releases/tag/v3.0.0"
        )
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        checker = UpdateChecker("2.0.4")
        result = checker.check_for_update()

        assert result is not None
        assert result[0] is True  # update_available
        assert result[1] == "3.0.0"  # latest_version (v stripped)
        assert "releases" in result[2]  # release_url

    @patch("anki_miner.services.update_checker.urllib.request.urlopen")
    def test_no_update_available(self, mock_urlopen):
        """Should return (False, version, url) when already up to date."""
        mock_response = MagicMock()
        mock_response.read.return_value = self._make_response(
            "v2.0.4", "https://github.com/0xzerolight/anki_miner/releases/tag/v2.0.4"
        )
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        checker = UpdateChecker("2.0.4")
        result = checker.check_for_update()

        assert result is not None
        assert result[0] is False  # no update

    @patch("anki_miner.services.update_checker.urllib.request.urlopen")
    def test_strips_v_prefix(self, mock_urlopen):
        """Should strip 'v' prefix from tag name."""
        mock_response = MagicMock()
        mock_response.read.return_value = self._make_response("v2.1.0", "https://example.com")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        checker = UpdateChecker("2.0.4")
        result = checker.check_for_update()

        assert result is not None
        assert result[1] == "2.1.0"

    @patch("anki_miner.services.update_checker.urllib.request.urlopen")
    def test_network_error_returns_none(self, mock_urlopen):
        """Should return None on network error."""
        mock_urlopen.side_effect = ConnectionError("No internet")

        checker = UpdateChecker("2.0.4")
        result = checker.check_for_update()

        assert result is None

    @patch("anki_miner.services.update_checker.urllib.request.urlopen")
    def test_timeout_returns_none(self, mock_urlopen):
        """Should return None on timeout."""
        from urllib.error import URLError

        mock_urlopen.side_effect = URLError("timed out")

        checker = UpdateChecker("2.0.4")
        result = checker.check_for_update()

        assert result is None

    @patch("anki_miner.services.update_checker.urllib.request.urlopen")
    def test_invalid_json_returns_none(self, mock_urlopen):
        """Should return None on invalid JSON response."""
        mock_response = MagicMock()
        mock_response.read.return_value = b"not json"
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        checker = UpdateChecker("2.0.4")
        result = checker.check_for_update()

        assert result is None

    @patch("anki_miner.services.update_checker.urllib.request.urlopen")
    def test_tag_without_v_prefix(self, mock_urlopen):
        """Should handle tag names without 'v' prefix."""
        mock_response = MagicMock()
        mock_response.read.return_value = self._make_response("2.1.0", "https://example.com")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        checker = UpdateChecker("2.0.4")
        result = checker.check_for_update()

        assert result is not None
        assert result[0] is True
        assert result[1] == "2.1.0"
