"""Tests for the shared version-compare helper."""

from anki_miner.utils.version_compare import is_newer


class TestIsNewer:
    def test_major_newer(self) -> None:
        assert is_newer("3.0.0", "2.0.4") is True

    def test_minor_newer(self) -> None:
        assert is_newer("2.1.0", "2.0.4") is True

    def test_patch_newer(self) -> None:
        assert is_newer("2.0.5", "2.0.4") is True

    def test_equal_is_not_newer(self) -> None:
        assert is_newer("2.0.4", "2.0.4") is False

    def test_older_is_not_newer(self) -> None:
        assert is_newer("2.0.3", "2.0.4") is False

    def test_older_major_is_not_newer(self) -> None:
        assert is_newer("1.9.9", "2.0.0") is False

    def test_unparseable_candidate_is_false(self) -> None:
        assert is_newer("abc", "2.0.4") is False

    def test_unparseable_current_is_false(self) -> None:
        assert is_newer("2.0.5", "abc") is False

    def test_both_empty_is_false(self) -> None:
        assert is_newer("", "") is False

    def test_short_versions_compare(self) -> None:
        assert is_newer("2.1", "2.0") is True

    def test_prerelease_newer_than_release(self) -> None:
        assert is_newer("2.4.0-rc1", "2.3.2") is True

    def test_release_older_than_prerelease(self) -> None:
        assert is_newer("2.3.2", "2.4.0-rc1") is False

    def test_post_release_newer(self) -> None:
        assert is_newer("2.3.5.post1", "2.3.5") is True

    def test_yt_dlp_date_versions(self) -> None:
        # yt-dlp uses date-based versions like 2024.03.10.
        assert is_newer("2024.03.10", "2024.02.01") is True
        assert is_newer("2024.02.01", "2024.03.10") is False
