"""Tests for episode_matcher module."""

from anki_miner.utils.episode_matcher import EpisodeMatcher, EpisodeNumberExtractor


class TestEpisodeNumberExtractor:
    """Tests for EpisodeNumberExtractor class."""

    class TestSeasonEpisodePatterns:
        """Tests for S01E01 style patterns."""

        def test_extracts_s01e01_format(self, tmp_path):
            """Should extract from S01E01 format."""
            path = tmp_path / "Anime_S01E05.mp4"
            path.touch()

            result = EpisodeNumberExtractor.extract_episode_info(path)

            assert result is not None
            assert result.episode_number == 5
            assert result.season_number == 1

        def test_extracts_lowercase_s01e01(self, tmp_path):
            """Should extract from lowercase s01e01 format."""
            path = tmp_path / "anime_s02e10.mkv"
            path.touch()

            result = EpisodeNumberExtractor.extract_episode_info(path)

            assert result is not None
            assert result.episode_number == 10
            assert result.season_number == 2

        def test_extracts_1x01_format(self, tmp_path):
            """Should extract from 1x01 format."""
            path = tmp_path / "Show_1x05.mp4"
            path.touch()

            result = EpisodeNumberExtractor.extract_episode_info(path)

            assert result is not None
            assert result.episode_number == 5
            assert result.season_number == 1

    class TestEpisodeOnlyPatterns:
        """Tests for episode-only patterns (no season)."""

        def test_extracts_ep01_format(self, tmp_path):
            """Should extract from ep01 format."""
            path = tmp_path / "Anime_ep01.mp4"
            path.touch()

            result = EpisodeNumberExtractor.extract_episode_info(path)

            assert result is not None
            assert result.episode_number == 1
            assert result.season_number is None

        def test_extracts_episode_01_format(self, tmp_path):
            """Should extract from episode_01 format."""
            path = tmp_path / "Show_episode_05.mp4"
            path.touch()

            result = EpisodeNumberExtractor.extract_episode_info(path)

            assert result is not None
            assert result.episode_number == 5

        def test_extracts_standalone_number(self, tmp_path):
            """Should extract standalone numbers."""
            path = tmp_path / "Anime_01.mp4"
            path.touch()

            result = EpisodeNumberExtractor.extract_episode_info(path)

            assert result is not None
            assert result.episode_number == 1

    class TestEdgeCases:
        """Tests for edge cases."""

        def test_returns_none_for_no_episode(self, tmp_path):
            """Should return None when no episode number found."""
            path = tmp_path / "no_episode_here.mp4"
            path.touch()

            result = EpisodeNumberExtractor.extract_episode_info(path)

            # May or may not find a number depending on filename
            # For this specific name, should not find anything useful
            assert result is None or result.episode_number is not None

        def test_ignores_extension(self, tmp_path):
            """Should ignore file extension when parsing."""
            path = tmp_path / "Anime_01.mp4"
            path.touch()

            result = EpisodeNumberExtractor.extract_episode_info(path)

            # Should not interpret mp4 as episode 4
            assert result.episode_number == 1

        def test_episode_info_has_filename_property(self, tmp_path):
            """EpisodeInfo should have filename property."""
            path = tmp_path / "Test_S01E01.mp4"
            path.touch()

            result = EpisodeNumberExtractor.extract_episode_info(path)

            assert result.filename == "Test_S01E01.mp4"

    class TestNumericTitlePrefix:
        """Regression for T-04 — a numeric show title must not steal the episode slot.

        The bare-number fallback used to take the FIRST number, so numeric
        titles ("86", "Mob Psycho 100", "Steins;Gate 0", "3-gatsu") collapsed
        every file onto the title number. The episode number is the LAST bare
        number on the line.
        """

        def test_86_title_takes_trailing_episode(self, tmp_path):
            path = tmp_path / "86 - 03.mkv"
            path.touch()
            result = EpisodeNumberExtractor.extract_episode_info(path)
            assert result is not None
            assert result.episode_number == 3

        def test_mob_psycho_100_takes_trailing_episode(self, tmp_path):
            path = tmp_path / "Mob Psycho 100 - 05.mkv"
            path.touch()
            result = EpisodeNumberExtractor.extract_episode_info(path)
            assert result is not None
            assert result.episode_number == 5

        def test_steins_gate_0_takes_trailing_episode(self, tmp_path):
            path = tmp_path / "Steins;Gate 0 - 07.mkv"
            path.touch()
            result = EpisodeNumberExtractor.extract_episode_info(path)
            assert result is not None
            assert result.episode_number == 7

        def test_3_gatsu_no_lion_takes_trailing_episode(self, tmp_path):
            path = tmp_path / "3-gatsu no Lion - 12.mkv"
            path.touch()
            result = EpisodeNumberExtractor.extract_episode_info(path)
            assert result is not None
            assert result.episode_number == 12

        def test_numeric_title_files_pair_distinctly(self, tmp_path):
            """Each numeric-title episode pairs with its own subtitle instead of
            collapsing onto episode 86."""
            video_dir = tmp_path / "videos"
            video_dir.mkdir()
            sub_dir = tmp_path / "subs"
            sub_dir.mkdir()
            vids, subs = [], []
            for n in range(1, 5):
                v = video_dir / f"86 - {n:02d}.mkv"
                v.touch()
                vids.append(v)
                s = sub_dir / f"86 - {n:02d}.srt"
                s.touch()
                subs.append(s)

            pairs = EpisodeMatcher.match_by_episode_number(vids, subs)

            assert len(pairs) == 4
            assert len({str(s) for _, s in pairs}) == 4
            for v, s in pairs:
                assert v.stem == s.stem

    class TestAdjacentBareNumbers:
        """Regression: BARE_NUMBER used to consume its trailing separator, so
        non-overlapping findall skipped a number that immediately followed a
        single-char-separated number — bare[-1] then picked the wrong episode.
        The trailing boundary is a lookahead so every run is captured.
        """

        def test_space_separated_trailing_number_wins(self, tmp_path):
            path = tmp_path / "Title 1 2.mkv"
            path.touch()
            result = EpisodeNumberExtractor.extract_episode_info(path)
            assert result is not None
            assert result.episode_number == 2

        def test_dash_chain_takes_last(self, tmp_path):
            path = tmp_path / "5-6-7.mkv"
            path.touch()
            result = EpisodeNumberExtractor.extract_episode_info(path)
            assert result is not None
            assert result.episode_number == 7

        def test_three_space_separated_numbers(self, tmp_path):
            path = tmp_path / "Show 1 2 3.mkv"
            path.touch()
            result = EpisodeNumberExtractor.extract_episode_info(path)
            assert result is not None
            assert result.episode_number == 3

    class TestSeasonEpisodeWithSeparator:
        """Regression for T-04 — SxxEyy must tolerate separators (whitespace/._-)
        between the season and episode tokens; 'S02 E05' was falling through to the
        bare-number fallback and mining the SEASON as the episode."""

        def test_s02_space_e05(self, tmp_path):
            path = tmp_path / "Show.S02 E05.mkv"
            path.touch()
            result = EpisodeNumberExtractor.extract_episode_info(path)
            assert result is not None
            assert result.season_number == 2
            assert result.episode_number == 5

        def test_s01_dot_e01(self, tmp_path):
            path = tmp_path / "Show.S01.E01.mkv"
            path.touch()
            result = EpisodeNumberExtractor.extract_episode_info(path)
            assert result is not None
            assert result.season_number == 1
            assert result.episode_number == 1

        def test_s03_dash_e10(self, tmp_path):
            path = tmp_path / "Show_S03-E10.mkv"
            path.touch()
            result = EpisodeNumberExtractor.extract_episode_info(path)
            assert result is not None
            assert result.season_number == 3
            assert result.episode_number == 10

    class TestResolutionInFilename:
        """Regression for Issue #36 — resolution must not be misread as season x episode."""

        def test_ignores_1280x720_in_filename(self, tmp_path):
            path = tmp_path / "[Coalgirls]_Show_01_(1280x720_Blu-ray_FLAC).ass"
            path.touch()
            result = EpisodeNumberExtractor.extract_episode_info(path)
            assert result is not None
            assert result.episode_number == 1
            assert result.season_number is None

        def test_ignores_1920x1080(self, tmp_path):
            path = tmp_path / "Anime.S02E05.1920x1080.mkv"
            path.touch()
            result = EpisodeNumberExtractor.extract_episode_info(path)
            assert result is not None
            assert result.season_number == 2
            assert result.episode_number == 5

        def test_ignores_720p_marker(self, tmp_path):
            path = tmp_path / "Show_03_720p.mp4"
            path.touch()
            result = EpisodeNumberExtractor.extract_episode_info(path)
            assert result is not None
            assert result.episode_number == 3

        def test_ignores_1080p_marker(self, tmp_path):
            path = tmp_path / "Show_07_1080p_BluRay.mkv"
            path.touch()
            result = EpisodeNumberExtractor.extract_episode_info(path)
            assert result is not None
            assert result.episode_number == 7

        def test_still_matches_legitimate_NxN_season_episode(self, tmp_path):
            path = tmp_path / "Friends_1x05.mp4"
            path.touch()
            result = EpisodeNumberExtractor.extract_episode_info(path)
            assert result is not None
            assert result.season_number == 1
            assert result.episode_number == 5


class TestEpisodeMatcher:
    """Tests for EpisodeMatcher class."""

    def test_matches_same_episode_numbers(self, tmp_path):
        """Should match files with same episode numbers."""
        # Create video files
        video_dir = tmp_path / "videos"
        video_dir.mkdir()
        video1 = video_dir / "Anime_01.mp4"
        video2 = video_dir / "Anime_02.mp4"
        video1.touch()
        video2.touch()

        # Create subtitle files
        sub_dir = tmp_path / "subs"
        sub_dir.mkdir()
        sub1 = sub_dir / "ep01.ass"
        sub2 = sub_dir / "ep02.ass"
        sub1.touch()
        sub2.touch()

        pairs = EpisodeMatcher.match_by_episode_number([video1, video2], [sub1, sub2])

        assert len(pairs) == 2
        # Pairs should be sorted by episode number
        assert pairs[0][0].name == "Anime_01.mp4"
        assert pairs[0][1].name == "ep01.ass"

    def test_matches_with_different_padding(self, tmp_path):
        """Should match files with different zero-padding."""
        video_dir = tmp_path / "videos"
        video_dir.mkdir()
        video = video_dir / "episode_1.mp4"  # No padding
        video.touch()

        sub_dir = tmp_path / "subs"
        sub_dir.mkdir()
        subtitle = sub_dir / "sub_01.ass"  # Zero-padded
        subtitle.touch()

        pairs = EpisodeMatcher.match_by_episode_number([video], [subtitle])

        assert len(pairs) == 1
        assert pairs[0][0] == video
        assert pairs[0][1] == subtitle

    def test_season_numbers_must_match(self, tmp_path):
        """Should not match if season numbers differ."""
        video_dir = tmp_path / "videos"
        video_dir.mkdir()
        video = video_dir / "S01E01.mp4"
        video.touch()

        sub_dir = tmp_path / "subs"
        sub_dir.mkdir()
        subtitle = sub_dir / "S02E01.ass"  # Different season
        subtitle.touch()

        pairs = EpisodeMatcher.match_by_episode_number([video], [subtitle])

        # Should not match - seasons differ
        assert len(pairs) == 0

    def test_returns_sorted_by_episode(self, tmp_path):
        """Should return pairs sorted by episode number."""
        video_dir = tmp_path / "videos"
        video_dir.mkdir()
        videos = []
        for n in [10, 2, 1]:
            v = video_dir / f"ep{n:02d}.mp4"
            v.touch()
            videos.append(v)

        sub_dir = tmp_path / "subs"
        sub_dir.mkdir()
        subs = []
        for n in [10, 2, 1]:
            s = sub_dir / f"sub{n:02d}.ass"
            s.touch()
            subs.append(s)

        pairs = EpisodeMatcher.match_by_episode_number(videos, subs)

        # Should be sorted: 1, 2, 10
        assert len(pairs) == 3
        assert "01" in pairs[0][0].name
        assert "02" in pairs[1][0].name
        assert "10" in pairs[2][0].name

    def test_handles_empty_lists(self, tmp_path):
        """Should handle empty file lists."""
        video = tmp_path / "ep01.mp4"
        video.touch()

        pairs = EpisodeMatcher.match_by_episode_number([video], [])

        assert pairs == []

    def test_resolution_in_name_produces_distinct_pairs(self, tmp_path):
        """Regression for Issue #36 — every episode must pair with its own subtitle,
        not collapse onto subtitle 01 because the resolution tag fooled the matcher."""
        video_dir = tmp_path / "videos"
        video_dir.mkdir()
        sub_dir = tmp_path / "subs"
        sub_dir.mkdir()
        vids, subs = [], []
        for n in range(1, 6):
            v = video_dir / f"Show_{n:02d}_(1280x720_BD).mkv"
            v.touch()
            vids.append(v)
            s = sub_dir / f"Show_{n:02d}_(1280x720_BD).ass"
            s.touch()
            subs.append(s)

        pairs = EpisodeMatcher.match_by_episode_number(vids, subs)

        assert len(pairs) == 5
        assert len({str(s) for _, s in pairs}) == 5
        # Each video should pair with its corresponding subtitle.
        for v, s in pairs:
            assert v.stem == s.stem

    def test_multiple_shows_same_episode_do_not_reuse_subtitle(self, tmp_path):
        """Regression for Issue #39 — when several videos share an episode number
        (multiple shows in one folder), each subtitle must be consumed at most once
        instead of every video collapsing onto the first matching subtitle."""
        video_dir = tmp_path / "videos"
        video_dir.mkdir()
        sub_dir = tmp_path / "subs"
        sub_dir.mkdir()
        vids, subs = [], []
        for show in ("ShowA", "ShowB", "ShowC"):
            v = video_dir / f"{show} - 01.mkv"
            v.touch()
            vids.append(v)
            s = sub_dir / f"{show} - 01.srt"
            s.touch()
            subs.append(s)

        pairs = EpisodeMatcher.match_by_episode_number(vids, subs)

        assert len(pairs) == 3
        # No subtitle is reused across pairs.
        assert len({str(s) for _, s in pairs}) == 3
        # Every video appears exactly once.
        assert len({str(v) for v, _ in pairs}) == 3

    def test_handles_no_matches(self, tmp_path):
        """Should return empty list when no matches found."""
        video_dir = tmp_path / "videos"
        video_dir.mkdir()
        video = video_dir / "ep01.mp4"
        video.touch()

        sub_dir = tmp_path / "subs"
        sub_dir.mkdir()
        subtitle = sub_dir / "ep99.ass"  # Different episode
        subtitle.touch()

        pairs = EpisodeMatcher.match_by_episode_number([video], [subtitle])

        assert len(pairs) == 0
