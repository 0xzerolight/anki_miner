"""Unit tests for find_sibling_subtitle helper (Task 7)."""

import pytest

from anki_miner.utils.file_pairing import SubtitleLanguage, find_sibling_subtitle


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

    def test_default_finds_vtt(self, tmp_path):
        """The mining default set includes .vtt, so a .vtt sibling autofills."""
        video = tmp_path / "episode01.mkv"
        video.touch()
        vtt = tmp_path / "episode01.vtt"
        vtt.touch()
        assert find_sibling_subtitle(video) == vtt

    def test_vtt_loses_to_every_richer_format(self, tmp_path):
        """.vtt sorts last, so an .srt sibling still wins outright."""
        video = tmp_path / "episode01.mkv"
        video.touch()
        srt = tmp_path / "episode01.srt"
        srt.touch()
        (tmp_path / "episode01.vtt").touch()
        assert find_sibling_subtitle(video) == srt

    def test_custom_priority_finds_vtt(self, tmp_path):
        """A caller-supplied priority including .vtt discovers the .vtt sibling."""
        video = tmp_path / "episode01.mkv"
        video.touch()
        vtt = tmp_path / "episode01.vtt"
        vtt.touch()
        assert find_sibling_subtitle(video, priority=(".ass", ".ssa", ".srt", ".vtt")) == vtt

    def test_custom_priority_ordering_respected(self, tmp_path):
        """The supplied priority order wins: .vtt first beats an existing .srt."""
        video = tmp_path / "ep01.mp4"
        video.touch()
        (tmp_path / "ep01.srt").touch()
        vtt = tmp_path / "ep01.vtt"
        vtt.touch()
        assert find_sibling_subtitle(video, priority=(".vtt", ".srt")) == vtt

    def test_explicit_default_priority_matches_implicit(self, tmp_path):
        """Passing the default set explicitly is byte-for-byte the None default."""
        from anki_miner.utils.file_pairing import DEFAULT_SUBTITLE_PRIORITY

        video = tmp_path / "ep01.mp4"
        video.touch()
        (tmp_path / "ep01.ass").touch()
        (tmp_path / "ep01.srt").touch()
        assert find_sibling_subtitle(video, priority=DEFAULT_SUBTITLE_PRIORITY) == find_sibling_subtitle(video)


JA = SubtitleLanguage(
    mining=frozenset({"ja", "jpn", "japanese", "jp"}),
    known=frozenset({"ja", "jpn", "japanese", "jp", "en", "eng", "english", "es", "spa", "it", "ita", "no", "nor"}),
)


def _folder(root, *names):
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    return root


class TestNamedForTheVideo:
    """Rule 2: the video's name plus tags."""

    @pytest.mark.parametrize(
        ("video", "subtitle"),
        [
            ("EP01.mkv", "EP01.ja.srt"),
            ("EP01.mkv", "EP01.jpn.forced.ass"),  # forced, but the only one
            ("Title [abc123].mp4", "Title [abc123].ja.vtt"),  # Utilities -> Download / yt-dlp
            ("EP01.mkv", "EP01.s2.jpn.ass"),  # Utilities -> Tracks, several tracks
            ("EP01.mkv", "EP01_track3_[jpn].ass"),  # mkvextract
            ("EP01.mkv", "ep01.Japanese.srt"),  # case-insensitive stem and tag
        ],
    )
    def test_pairs_a_tagged_name(self, tmp_path, video, subtitle):
        _folder(tmp_path, video, subtitle)
        assert find_sibling_subtitle(tmp_path / video, language=JA) == tmp_path / subtitle

    def test_mining_language_beats_another(self, tmp_path):
        _folder(tmp_path, "EP01.mkv", "EP01.en.srt", "EP01.ja.srt")
        assert find_sibling_subtitle(tmp_path / "EP01.mkv", language=JA) == tmp_path / "EP01.ja.srt"

    def test_another_language_alone_is_never_filled(self, tmp_path):
        _folder(tmp_path, "EP01.mkv", "EP01.en.srt")
        assert find_sibling_subtitle(tmp_path / "EP01.mkv", language=JA) is None

    def test_full_track_beats_forced(self, tmp_path):
        _folder(tmp_path, "Movie.mkv", "Movie.ja.forced.srt", "Movie.ja.srt")
        assert find_sibling_subtitle(tmp_path / "Movie.mkv", language=JA) == tmp_path / "Movie.ja.srt"

    def test_tie_leaves_picker_empty(self, tmp_path):
        _folder(tmp_path, "EP01.mkv", "EP01.ja.srt", "EP01.jpn.srt")
        assert find_sibling_subtitle(tmp_path / "EP01.mkv", language=JA) is None

    def test_exact_stem_still_wins(self, tmp_path):
        _folder(tmp_path, "EP01.mkv", "EP01.srt", "EP01.ja.ass")
        assert find_sibling_subtitle(tmp_path / "EP01.mkv", language=JA) == tmp_path / "EP01.srt"

    def test_a_longer_episode_number_is_not_a_tag(self, tmp_path):
        _folder(tmp_path, "EP1.mkv", "EP10.ja.srt", "EP2.mkv")
        assert find_sibling_subtitle(tmp_path / "EP1.mkv", language=JA) is None

    def test_longer_media_name_owns_its_subtitle(self, tmp_path):
        _folder(tmp_path, "Toy Story.mkv", "Toy Story 2.mkv", "Toy Story 2.ja.srt")
        assert find_sibling_subtitle(tmp_path / "Toy Story.mkv", language=JA) is None
        assert find_sibling_subtitle(tmp_path / "Toy Story 2.mkv", language=JA) == tmp_path / "Toy Story 2.ja.srt"

    def test_condensed_output_is_not_the_videos_subtitle(self, tmp_path):
        """Condense writes ep01_condensed.mp3 + ep01_condensed.srt beside the video by default."""
        _folder(tmp_path, "ep01.mkv", "ep01_condensed.mp3", "ep01_condensed.srt")
        assert find_sibling_subtitle(tmp_path / "ep01.mkv", language=JA) is None

    @pytest.mark.parametrize(
        ("video", "subtitle"),
        [
            ("EP01.mkv", "EP01_track4_[eng].ass"),  # mkvextract / gMKVExtractGUI
            ("Show - 01.mkv", "Show - 01 [eng].srt"),
            ("Show - 01.mkv", "Show - 01.ENG.srt"),
            ("Movie.mkv", "Movie.en.sdh.forced.srt"),  # Plex
            ("Show - 01.mkv", "Show - 01.en.default.forced.srt"),  # Jellyfin
            ("Movie.mkv", "Movie.English (SDH).srt"),
        ],
    )
    def test_another_language_is_never_filled_whatever_the_tool_named_it(self, tmp_path, video, subtitle):
        """Final review, Important 1: the looser rules re-read these as untagged and filled them."""
        _folder(tmp_path, video, subtitle)
        assert find_sibling_subtitle(tmp_path / video, language=JA) is None

    def test_episode_title_words_are_not_tags(self, tmp_path):
        """'It' in an episode title is not Italian, 'no' in 'Kimi no Na wa' not Norwegian."""
        _folder(
            tmp_path, "Show - 01.mkv", "Show - 02.mkv", "Show - 01 - Just Do It.srt", "Show - 02 - Kimi no Na wa.srt"
        )
        assert find_sibling_subtitle(tmp_path / "Show - 01.mkv", language=JA) == tmp_path / "Show - 01 - Just Do It.srt"
        assert (
            find_sibling_subtitle(tmp_path / "Show - 02.mkv", language=JA) == tmp_path / "Show - 02 - Kimi no Na wa.srt"
        )


class TestSameEpisodeNumber:
    """Rule 3: differently named subtitles beside the video (Jimaku, Kitsunekko)."""

    def test_pairs_by_episode_number(self, tmp_path):
        _folder(
            tmp_path,
            "[SubsPlease] Frieren - 01 (1080p) [ABCD1234].mkv",
            "[SubsPlease] Frieren - 02 (1080p) [ABCD5678].mkv",
            "Sousou no Frieren.S01E01.ja.srt",
            "Sousou no Frieren.S01E02.ja.srt",
        )
        video = tmp_path / "[SubsPlease] Frieren - 02 (1080p) [ABCD5678].mkv"
        assert find_sibling_subtitle(video, language=JA) == tmp_path / "Sousou no Frieren.S01E02.ja.srt"

    def test_prefers_the_mining_language(self, tmp_path):
        _folder(tmp_path, "Show - 01.mkv", "Show - 02.mkv", "x.E01.en.srt", "x.E01.ja.srt")
        assert find_sibling_subtitle(tmp_path / "Show - 01.mkv", language=JA) == tmp_path / "x.E01.ja.srt"

    def test_episode_rule_skips_when_another_video_shares_the_number(self, tmp_path):
        _folder(tmp_path, "Show - 01.mkv", "Show - 01 NCOP.mkv", "x - 01.ja.srt")
        assert find_sibling_subtitle(tmp_path / "Show - 01.mkv", language=JA) is None

    def test_season_mismatch_does_not_pair(self, tmp_path):
        _folder(tmp_path, "Show S02E01.mkv", "Show S02E02.mkv", "Show S01E01.ja.srt")
        assert find_sibling_subtitle(tmp_path / "Show S02E01.mkv", language=JA) is None

    def test_two_groups_tie(self, tmp_path):
        _folder(tmp_path, "Show - 01.mkv", "Show - 02.mkv", "[A] Show - 01.ass", "[B] Show - 01.ass")
        assert find_sibling_subtitle(tmp_path / "Show - 01.mkv", language=JA) is None

    def test_seasonless_subtitle_is_not_given_to_two_seasons(self, tmp_path):
        _folder(tmp_path, "Show S01E01.mkv", "Show S02E01.mkv", "Show - 01.ja.srt")
        assert find_sibling_subtitle(tmp_path / "Show S01E01.mkv", language=JA) is None
        assert find_sibling_subtitle(tmp_path / "Show S02E01.mkv", language=JA) is None

    def test_season_named_subtitle_still_pairs_beside_another_season(self, tmp_path):
        _folder(tmp_path, "Show S01E01.mkv", "Show S02E01.mkv", "x S01E01.ja.srt")
        assert find_sibling_subtitle(tmp_path / "Show S01E01.mkv", language=JA) == tmp_path / "x S01E01.ja.srt"


class TestLonePair:
    """Rule 4: the only video in its folder takes the only usable subtitle."""

    def test_movie_pairs_with_its_subtitle(self, tmp_path):
        _folder(tmp_path, "Movie.2019.1080p.BluRay.x264.mkv", "Movie.2019.ja.srt", "Movie.2019.en.srt")
        video = tmp_path / "Movie.2019.1080p.BluRay.x264.mkv"
        assert find_sibling_subtitle(video, language=JA) == tmp_path / "Movie.2019.ja.srt"

    def test_lone_pair_refuses_a_different_episode(self, tmp_path):
        _folder(tmp_path, "Show - 01.mkv", "Show - 02.ja.srt")
        assert find_sibling_subtitle(tmp_path / "Show - 01.mkv", language=JA) is None

    def test_not_lone_when_another_video_is_there(self, tmp_path):
        _folder(tmp_path, "Movie.mkv", "Extras.mkv", "subtitle.srt")
        assert find_sibling_subtitle(tmp_path / "Movie.mkv", language=JA) is None

    def test_not_lone_beside_a_downloaded_audio_file(self, tmp_path):
        """Utilities -> Download folders mix "Title [id].mp4" with "Title [id].m4a"/.webm downloads."""
        _folder(tmp_path, "Video B [xyz].mp4", "Song A [abc].m4a", "Song A [abc].ja.srt")
        assert find_sibling_subtitle(tmp_path / "Video B [xyz].mp4", language=JA) is None

    def test_works_for_audio(self, tmp_path):
        _folder(tmp_path, "Book.m4b", "Transcript.srt")
        assert find_sibling_subtitle(tmp_path / "Book.m4b", language=JA) == tmp_path / "Transcript.srt"

    def test_appledouble_sidecar_is_ignored(self, tmp_path):
        _folder(tmp_path, "Movie.mkv", "Movie.ja.srt")
        (tmp_path / "._Movie.ja.srt").write_bytes(b"\x00\x05\x16\x07")
        assert find_sibling_subtitle(tmp_path / "Movie.mkv", language=JA) == tmp_path / "Movie.ja.srt"


class TestSubsFolder:
    """A ``Subs`` folder beside the video (release-rip layout)."""

    def test_named_sub_in_subs_folder(self, tmp_path):
        _folder(tmp_path, "EP01.mkv", "EP02.mkv", "Subs/EP01.ja.srt", "Subs/EP02.ja.srt")
        assert find_sibling_subtitle(tmp_path / "EP01.mkv", language=JA) == tmp_path / "Subs" / "EP01.ja.srt"

    def test_per_episode_folder(self, tmp_path):
        _folder(tmp_path, "EP01.mkv", "EP02.mkv", "subs/EP01/2_English.srt", "subs/EP01/3_Japanese.srt")
        expected = tmp_path / "subs" / "EP01" / "3_Japanese.srt"
        assert find_sibling_subtitle(tmp_path / "EP01.mkv", language=JA) == expected

    def test_video_folder_comes_first(self, tmp_path):
        _folder(tmp_path, "EP01.mkv", "EP01.ja.srt", "Subs/EP01.ja.srt")
        assert find_sibling_subtitle(tmp_path / "EP01.mkv", language=JA) == tmp_path / "EP01.ja.srt"

    def test_other_folders_are_not_searched(self, tmp_path):
        _folder(tmp_path, "EP01.mkv", "Extras/EP01.ja.srt")
        assert find_sibling_subtitle(tmp_path / "EP01.mkv", language=JA) is None

    def test_named_in_subs_beats_a_guess_beside_the_video(self, tmp_path):
        _folder(tmp_path, "Movie.mkv", "signs.srt", "Subs/Movie.ja.srt")
        assert find_sibling_subtitle(tmp_path / "Movie.mkv", language=JA) == tmp_path / "Subs" / "Movie.ja.srt"
