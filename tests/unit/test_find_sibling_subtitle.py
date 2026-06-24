"""Unit tests for find_sibling_subtitle helper (Task 7)."""

from anki_miner.utils.file_pairing import find_sibling_subtitle


class TestFindSiblingSubtitle:
    """Tests for find_sibling_subtitle."""

    def test_returns_none_when_no_sibling_exists(self, tmp_path):
        """Returns None when no subtitle sibling is present."""
        video = tmp_path / "episode01.mkv"
        video.touch()
        assert find_sibling_subtitle(video) is None

    def test_finds_srt_sibling(self, tmp_path):
        """Returns .srt sibling when that is the only subtitle present."""
        video = tmp_path / "episode01.mkv"
        video.touch()
        srt = tmp_path / "episode01.srt"
        srt.touch()
        assert find_sibling_subtitle(video) == srt

    def test_finds_sibling_case_insensitively(self, tmp_path):
        """An uppercase .SRT extension is still matched (M6, case-sensitive FS)."""
        video = tmp_path / "episode01.mkv"
        video.touch()
        srt = tmp_path / "episode01.SRT"
        srt.touch()
        assert find_sibling_subtitle(video) == srt

    def test_ass_beats_ssa_beats_srt(self, tmp_path):
        """Priority: .ass > .ssa > .srt."""
        video = tmp_path / "ep01.mp4"
        video.touch()
        for ext in (".ass", ".ssa", ".srt"):
            (tmp_path / f"ep01{ext}").touch()
        assert find_sibling_subtitle(video) == tmp_path / "ep01.ass"

    def test_ass_beats_srt_without_ssa(self, tmp_path):
        """.ass wins over .srt when .ssa is absent."""
        video = tmp_path / "ep01.mp4"
        video.touch()
        (tmp_path / "ep01.ass").touch()
        (tmp_path / "ep01.srt").touch()
        assert find_sibling_subtitle(video) == tmp_path / "ep01.ass"

    def test_ssa_beats_srt_without_ass(self, tmp_path):
        """.ssa wins over .srt when .ass is absent."""
        video = tmp_path / "ep01.mp4"
        video.touch()
        (tmp_path / "ep01.ssa").touch()
        (tmp_path / "ep01.srt").touch()
        assert find_sibling_subtitle(video) == tmp_path / "ep01.ssa"

    def test_stem_match_only_same_folder(self, tmp_path):
        """Only files with the exact same stem in the same folder are returned."""
        video = tmp_path / "episode01.mkv"
        video.touch()
        # Wrong stem — must not match
        (tmp_path / "episode02.srt").touch()
        assert find_sibling_subtitle(video) is None

    def test_different_folder_not_picked(self, tmp_path):
        """Subtitles in a sibling folder are not returned."""
        folder_a = tmp_path / "a"
        folder_a.mkdir()
        folder_b = tmp_path / "b"
        folder_b.mkdir()
        video = folder_a / "ep01.mkv"
        video.touch()
        (folder_b / "ep01.srt").touch()
        assert find_sibling_subtitle(video) is None

    def test_reuses_default_subtitle_priority(self, tmp_path):
        """Result uses DEFAULT_SUBTITLE_PRIORITY ordering (smoke check)."""
        from anki_miner.utils.file_pairing import DEFAULT_SUBTITLE_PRIORITY

        video = tmp_path / "ep01.mkv"
        video.touch()
        # Create only the lowest-priority format
        lowest_ext = DEFAULT_SUBTITLE_PRIORITY[-1]
        sibling = tmp_path / f"ep01{lowest_ext}"
        sibling.touch()
        assert find_sibling_subtitle(video) == sibling
