"""Tests for drag-and-drop file classification logic."""

from pathlib import Path

from anki_miner.gui.widgets.single_episode_tab import (
    SUBTITLE_EXTENSIONS,
    VIDEO_EXTENSIONS,
)


class TestFileExtensionClassification:
    """Tests for file extension sets used in drag-and-drop routing."""

    def test_video_extensions_contains_mp4(self):
        assert ".mp4" in VIDEO_EXTENSIONS

    def test_video_extensions_contains_mkv(self):
        assert ".mkv" in VIDEO_EXTENSIONS

    def test_video_extensions_contains_avi(self):
        assert ".avi" in VIDEO_EXTENSIONS

    def test_video_extensions_contains_m4v(self):
        assert ".m4v" in VIDEO_EXTENSIONS

    def test_video_extensions_contains_mov(self):
        assert ".mov" in VIDEO_EXTENSIONS

    def test_subtitle_extensions_contains_ass(self):
        assert ".ass" in SUBTITLE_EXTENSIONS

    def test_subtitle_extensions_contains_srt(self):
        assert ".srt" in SUBTITLE_EXTENSIONS

    def test_subtitle_extensions_contains_ssa(self):
        assert ".ssa" in SUBTITLE_EXTENSIONS

    def test_no_overlap_between_video_and_subtitle(self):
        assert VIDEO_EXTENSIONS.isdisjoint(SUBTITLE_EXTENSIONS)


class TestFileRouting:
    """Tests for the file routing logic used by drag-and-drop."""

    def test_video_file_detected(self):
        """Video file should be classified as video."""
        for ext in VIDEO_EXTENSIONS:
            path = Path(f"/test/episode{ext}")
            assert path.suffix.lower() in VIDEO_EXTENSIONS

    def test_subtitle_file_detected(self):
        """Subtitle file should be classified as subtitle."""
        for ext in SUBTITLE_EXTENSIONS:
            path = Path(f"/test/episode{ext}")
            assert path.suffix.lower() in SUBTITLE_EXTENSIONS

    def test_unknown_extension_not_classified(self):
        """Unknown extensions should not match either category."""
        unknown_files = [
            Path("/test/readme.txt"),
            Path("/test/image.png"),
            Path("/test/data.json"),
        ]
        for path in unknown_files:
            assert path.suffix.lower() not in VIDEO_EXTENSIONS
            assert path.suffix.lower() not in SUBTITLE_EXTENSIONS

    def test_case_insensitive_matching(self):
        """Extension matching should work case-insensitively via .lower()."""
        path = Path("/test/Episode.MKV")
        assert path.suffix.lower() in VIDEO_EXTENSIONS

        path2 = Path("/test/Episode.ASS")
        assert path2.suffix.lower() in SUBTITLE_EXTENSIONS

    def test_multiple_files_routing(self):
        """Simulate routing multiple dropped files to correct selectors."""
        files = [
            Path("/test/ep01.mkv"),
            Path("/test/ep01.ass"),
            Path("/test/readme.txt"),
        ]

        video_paths = []
        subtitle_paths = []

        for f in files:
            suffix = f.suffix.lower()
            if suffix in VIDEO_EXTENSIONS:
                video_paths.append(f)
            elif suffix in SUBTITLE_EXTENSIONS:
                subtitle_paths.append(f)

        assert len(video_paths) == 1
        assert len(subtitle_paths) == 1
        assert video_paths[0].name == "ep01.mkv"
        assert subtitle_paths[0].name == "ep01.ass"
