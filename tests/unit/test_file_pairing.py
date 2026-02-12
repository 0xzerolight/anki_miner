"""Tests for file_pairing module."""

from anki_miner.utils.file_pairing import FilePair, FilePairMatcher


class TestFilePair:
    """Tests for FilePair dataclass."""

    def test_video_name_property(self, tmp_path):
        """Should return video filename."""
        video = tmp_path / "video.mp4"
        subtitle = tmp_path / "sub.ass"
        video.touch()
        subtitle.touch()

        pair = FilePair(video, subtitle)

        assert pair.video_name == "video.mp4"

    def test_subtitle_name_property(self, tmp_path):
        """Should return subtitle filename."""
        video = tmp_path / "video.mp4"
        subtitle = tmp_path / "sub.ass"
        video.touch()
        subtitle.touch()

        pair = FilePair(video, subtitle)

        assert pair.subtitle_name == "sub.ass"


class TestFilePairMatcher:
    """Tests for FilePairMatcher class."""

    class TestFindPairsAcrossFolders:
        """Tests for find_pairs_across_folders method."""

        def test_matches_by_base_name(self, tmp_path):
            """Should match files with same base name."""
            anime_dir = tmp_path / "anime"
            anime_dir.mkdir()
            sub_dir = tmp_path / "subs"
            sub_dir.mkdir()

            video = anime_dir / "episode_01.mp4"
            subtitle = sub_dir / "episode_01.ass"
            video.touch()
            subtitle.touch()

            pairs = FilePairMatcher.find_pairs_across_folders(anime_dir, sub_dir)

            assert len(pairs) == 1
            assert pairs[0].video == video
            assert pairs[0].subtitle == subtitle

        def test_matches_multiple_pairs(self, tmp_path):
            """Should match multiple file pairs."""
            anime_dir = tmp_path / "anime"
            anime_dir.mkdir()
            sub_dir = tmp_path / "subs"
            sub_dir.mkdir()

            for i in range(1, 4):
                (anime_dir / f"ep{i:02d}.mp4").touch()
                (sub_dir / f"ep{i:02d}.srt").touch()

            pairs = FilePairMatcher.find_pairs_across_folders(anime_dir, sub_dir)

            assert len(pairs) == 3

        def test_different_subtitle_extensions(self, tmp_path):
            """Should match with different subtitle extensions."""
            anime_dir = tmp_path / "anime"
            anime_dir.mkdir()
            sub_dir = tmp_path / "subs"
            sub_dir.mkdir()

            (anime_dir / "ep01.mkv").touch()
            (anime_dir / "ep02.mkv").touch()
            (anime_dir / "ep03.mkv").touch()
            (sub_dir / "ep01.ass").touch()
            (sub_dir / "ep02.srt").touch()
            (sub_dir / "ep03.ssa").touch()

            pairs = FilePairMatcher.find_pairs_across_folders(anime_dir, sub_dir)

            assert len(pairs) == 3

        def test_returns_naturally_sorted(self, tmp_path):
            """Should return pairs naturally sorted by video name."""
            anime_dir = tmp_path / "anime"
            anime_dir.mkdir()
            sub_dir = tmp_path / "subs"
            sub_dir.mkdir()

            # Create in non-sorted order
            for n in [10, 2, 1]:
                (anime_dir / f"ep{n}.mp4").touch()
                (sub_dir / f"ep{n}.ass").touch()

            pairs = FilePairMatcher.find_pairs_across_folders(anime_dir, sub_dir)

            # Should be sorted: 1, 2, 10
            names = [p.video_name for p in pairs]
            assert names == ["ep1.mp4", "ep2.mp4", "ep10.mp4"]

        def test_ignores_non_video_files(self, tmp_path):
            """Should ignore non-video files in anime folder."""
            anime_dir = tmp_path / "anime"
            anime_dir.mkdir()
            sub_dir = tmp_path / "subs"
            sub_dir.mkdir()

            (anime_dir / "ep01.mp4").touch()
            (anime_dir / "ep01.txt").touch()  # Not a video
            (sub_dir / "ep01.ass").touch()

            pairs = FilePairMatcher.find_pairs_across_folders(anime_dir, sub_dir)

            assert len(pairs) == 1

        def test_handles_empty_folders(self, tmp_path):
            """Should return empty list for empty folders."""
            anime_dir = tmp_path / "anime"
            anime_dir.mkdir()
            sub_dir = tmp_path / "subs"
            sub_dir.mkdir()

            pairs = FilePairMatcher.find_pairs_across_folders(anime_dir, sub_dir)

            assert pairs == []

        def test_handles_no_matching_subtitles(self, tmp_path):
            """Should return empty when no matching subtitles."""
            anime_dir = tmp_path / "anime"
            anime_dir.mkdir()
            sub_dir = tmp_path / "subs"
            sub_dir.mkdir()

            (anime_dir / "ep01.mp4").touch()
            (sub_dir / "ep02.ass").touch()  # Different name

            pairs = FilePairMatcher.find_pairs_across_folders(anime_dir, sub_dir)

            assert pairs == []

    class TestFindUnpairedFiles:
        """Tests for find_unpaired_files method."""

        def test_finds_unpaired_videos(self, tmp_path):
            """Should find videos without matching subtitles."""
            anime_dir = tmp_path / "anime"
            anime_dir.mkdir()
            sub_dir = tmp_path / "subs"
            sub_dir.mkdir()

            (anime_dir / "ep01.mp4").touch()
            (anime_dir / "ep02.mp4").touch()
            (sub_dir / "ep01.ass").touch()  # Only ep01 has subtitle

            unpaired_videos, unpaired_subs = FilePairMatcher.find_unpaired_files(anime_dir, sub_dir)

            assert len(unpaired_videos) == 1
            assert unpaired_videos[0].name == "ep02.mp4"
            assert len(unpaired_subs) == 0

        def test_finds_unpaired_subtitles(self, tmp_path):
            """Should find subtitles without matching videos."""
            anime_dir = tmp_path / "anime"
            anime_dir.mkdir()
            sub_dir = tmp_path / "subs"
            sub_dir.mkdir()

            (anime_dir / "ep01.mp4").touch()
            (sub_dir / "ep01.ass").touch()
            (sub_dir / "ep02.ass").touch()  # No matching video

            unpaired_videos, unpaired_subs = FilePairMatcher.find_unpaired_files(anime_dir, sub_dir)

            assert len(unpaired_videos) == 0
            assert len(unpaired_subs) == 1
            assert unpaired_subs[0].name == "ep02.ass"

    class TestFindPairsByEpisodeNumber:
        """Tests for find_pairs_by_episode_number method."""

        def test_matches_by_episode_number(self, tmp_path):
            """Should match files with same episode number."""
            anime_dir = tmp_path / "anime"
            anime_dir.mkdir()
            sub_dir = tmp_path / "subs"
            sub_dir.mkdir()

            # Different naming conventions, same episode
            (anime_dir / "Anime_S01E01.mkv").touch()
            (sub_dir / "ep01.ass").touch()

            pairs = FilePairMatcher.find_pairs_by_episode_number(anime_dir, sub_dir)

            assert len(pairs) == 1
            assert pairs[0].video.name == "Anime_S01E01.mkv"
            assert pairs[0].subtitle.name == "ep01.ass"

        def test_returns_filepair_objects(self, tmp_path):
            """Should return FilePair objects."""
            anime_dir = tmp_path / "anime"
            anime_dir.mkdir()
            sub_dir = tmp_path / "subs"
            sub_dir.mkdir()

            (anime_dir / "ep01.mp4").touch()
            (sub_dir / "ep01.ass").touch()

            pairs = FilePairMatcher.find_pairs_by_episode_number(anime_dir, sub_dir)

            assert len(pairs) == 1
            assert isinstance(pairs[0], FilePair)

        def test_handles_different_padding(self, tmp_path):
            """Should match episodes with different zero-padding."""
            anime_dir = tmp_path / "anime"
            anime_dir.mkdir()
            sub_dir = tmp_path / "subs"
            sub_dir.mkdir()

            (anime_dir / "episode_1.mp4").touch()  # No padding
            (sub_dir / "sub_01.ass").touch()  # Zero-padded

            pairs = FilePairMatcher.find_pairs_by_episode_number(anime_dir, sub_dir)

            assert len(pairs) == 1
