"""Tests for file_pairing module."""

from anki_miner.utils.file_pairing import FilePair, FilePairMatcher


class TestFilePair:
    """Tests for FilePair dataclass."""

    def test_stores_video_and_subtitle(self, tmp_path):
        """Should store provided video and subtitle paths."""
        video = tmp_path / "video.mp4"
        subtitle = tmp_path / "sub.ass"
        video.touch()
        subtitle.touch()

        pair = FilePair(video, subtitle)

        assert pair.video == video
        assert pair.subtitle == subtitle


class TestFilePairMatcher:
    """Tests for FilePairMatcher class."""

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
