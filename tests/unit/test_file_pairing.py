"""Tests for file_pairing module."""

import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

from anki_miner.utils import file_pairing
from anki_miner.utils.file_pairing import FilePair, FilePairMatcher, resolve_output_path

# A name with a dakuten kana that genuinely decomposes under NFD. Common kana
# (ねこ, 東京, ファ) are byte-identical in NFC vs NFD and would NOT exercise the
# bug — guard that with an assertion in the fixture below.
_DECOMPOSING_STEM = "が01"
_NFC_NAME = unicodedata.normalize("NFC", _DECOMPOSING_STEM) + ".srt"
_NFD_NAME = unicodedata.normalize("NFD", _DECOMPOSING_STEM) + ".srt"


def test_decomposing_fixture_actually_diverges():
    """Self-check: the chosen name must differ in bytes between NFC and NFD,
    else every NFC/NFD test below is a false green."""
    assert _NFC_NAME.encode("utf-8") != _NFD_NAME.encode("utf-8")


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
            video_dir = tmp_path / "video"
            video_dir.mkdir()
            sub_dir = tmp_path / "subs"
            sub_dir.mkdir()

            # Different naming conventions, same episode
            (video_dir / "Anime_S01E01.mkv").touch()
            (sub_dir / "ep01.ass").touch()

            pairs = FilePairMatcher.find_pairs_by_episode_number(video_dir, sub_dir)

            assert len(pairs) == 1
            assert pairs[0].video.name == "Anime_S01E01.mkv"
            assert pairs[0].subtitle.name == "ep01.ass"

        def test_returns_filepair_objects(self, tmp_path):
            """Should return FilePair objects."""
            video_dir = tmp_path / "video"
            video_dir.mkdir()
            sub_dir = tmp_path / "subs"
            sub_dir.mkdir()

            (video_dir / "ep01.mp4").touch()
            (sub_dir / "ep01.ass").touch()

            pairs = FilePairMatcher.find_pairs_by_episode_number(video_dir, sub_dir)

            assert len(pairs) == 1
            assert isinstance(pairs[0], FilePair)

        def test_handles_different_padding(self, tmp_path):
            """Should match episodes with different zero-padding."""
            video_dir = tmp_path / "video"
            video_dir.mkdir()
            sub_dir = tmp_path / "subs"
            sub_dir.mkdir()

            (video_dir / "episode_1.mp4").touch()  # No padding
            (sub_dir / "sub_01.ass").touch()  # Zero-padded

            pairs = FilePairMatcher.find_pairs_by_episode_number(video_dir, sub_dir)

            assert len(pairs) == 1

        def test_default_subtitle_extensions_pair_vtt(self, tmp_path):
            """.vtt is in the mining default set, so a .vtt subtitle pairs with
            no caller-supplied extension set."""
            video_dir = tmp_path / "video"
            video_dir.mkdir()
            sub_dir = tmp_path / "subs"
            sub_dir.mkdir()

            (video_dir / "Show_01.mkv").touch()
            (sub_dir / "Show_01.vtt").touch()

            pairs = FilePairMatcher.find_pairs_by_episode_number(video_dir, sub_dir)

            assert len(pairs) == 1
            assert pairs[0].subtitle.name == "Show_01.vtt"

        def test_richer_format_outranks_vtt_by_default(self, tmp_path):
            """With both present the default priority prefers .srt over .vtt."""
            video_dir = tmp_path / "video"
            video_dir.mkdir()
            sub_dir = tmp_path / "subs"
            sub_dir.mkdir()

            (video_dir / "Show_01.mkv").touch()
            (sub_dir / "Show_01.vtt").touch()
            (sub_dir / "Show_01.srt").touch()

            pairs = FilePairMatcher.find_pairs_by_episode_number(video_dir, sub_dir)

            assert [pair.subtitle.name for pair in pairs] == ["Show_01.srt"]

        def test_custom_video_extensions_finds_audio(self, tmp_path):
            """Audio-only inputs pair when the caller supplies audio media
            extensions (condenser use case, D12)."""
            video_dir = tmp_path / "video"
            video_dir.mkdir()
            sub_dir = tmp_path / "subs"
            sub_dir.mkdir()

            (video_dir / "Show_01.m4b").touch()
            (sub_dir / "Show_01.srt").touch()

            # Default video set excludes .m4b → no pair.
            assert FilePairMatcher.find_pairs_by_episode_number(video_dir, sub_dir) == []

            pairs = FilePairMatcher.find_pairs_by_episode_number(
                video_dir, sub_dir, video_extensions=frozenset({".m4b"})
            )
            assert len(pairs) == 1
            assert pairs[0].video.name == "Show_01.m4b"

        def test_explicit_default_extensions_match_implicit(self, tmp_path):
            """Passing the class-default extension sets explicitly reproduces the
            None-default result exactly (byte-for-byte parity)."""
            video_dir = tmp_path / "video"
            video_dir.mkdir()
            sub_dir = tmp_path / "subs"
            sub_dir.mkdir()

            (video_dir / "Show_01.mkv").touch()
            (sub_dir / "Show_01.srt").touch()

            explicit = FilePairMatcher.find_pairs_by_episode_number(
                video_dir,
                sub_dir,
                video_extensions=FilePairMatcher.VIDEO_EXTENSIONS,
                subtitle_extensions=FilePairMatcher.SUBTITLE_EXTENSIONS,
            )
            implicit = FilePairMatcher.find_pairs_by_episode_number(video_dir, sub_dir)
            assert explicit == implicit
            assert len(implicit) == 1

        def test_pairs_sorted_by_episode_ascending(self, tmp_path):
            """The batch queue consumes this order, so it must be ascending by
            episode number regardless of filesystem iteration order (Issue #80)."""
            video_dir = tmp_path / "video"
            video_dir.mkdir()
            sub_dir = tmp_path / "subs"
            sub_dir.mkdir()

            # Created out of order; result must still be 01, 02, 03.
            for n in (3, 1, 2):
                (video_dir / f"Show_{n:02d}.mkv").touch()
                (sub_dir / f"Show_{n:02d}.srt").touch()

            pairs = FilePairMatcher.find_pairs_by_episode_number(video_dir, sub_dir)

            assert [p.video.name for p in pairs] == ["Show_01.mkv", "Show_02.mkv", "Show_03.mkv"]

        @pytest.mark.parametrize(
            ("subtitle_names", "subtitle_extensions", "expected"),
            [
                (("Show_01.srt", "Show_01.ssa", "Show_01.ass"), None, "Show_01.ass"),
                (("Show_01.srt", "Show_01.ssa"), None, "Show_01.ssa"),
                (("Show_01.vtt", "Show_01.srt"), frozenset({".vtt", ".srt"}), "Show_01.srt"),
                # A format in DEFAULT_SUBTITLE_PRIORITY outranks one that is not,
                # whatever the names: .vtt is ranked, .sub (MicroDVD) is not.
                (("Zulu_01.vtt", "Alpha_01.sub"), frozenset({".vtt", ".sub"}), "Zulu_01.vtt"),
                # Two equally unranked formats fall through to the name ordering.
                (("Zulu_01.sub", "Alpha_01.idx"), frozenset({".sub", ".idx"}), "Alpha_01.idx"),
                (("Zulu_01.vtt", "Alpha_01.vtt"), frozenset({".vtt"}), "Alpha_01.vtt"),
            ],
        )
        def test_subtitle_priority_is_independent_of_directory_order(
            self,
            tmp_path,
            monkeypatch,
            subtitle_names,
            subtitle_extensions,
            expected,
        ):
            video_dir = tmp_path / "video"
            video_dir.mkdir()
            sub_dir = tmp_path / "subs"
            sub_dir.mkdir()
            video = video_dir / "Show_01.mkv"
            video.touch()
            subtitles = [sub_dir / name for name in subtitle_names]
            for subtitle in subtitles:
                subtitle.touch()

            entries = {video_dir: [video], sub_dir: subtitles}
            monkeypatch.setattr(Path, "iterdir", lambda path: iter(entries[path]))

            pairs = FilePairMatcher.find_pairs_by_episode_number(
                video_dir,
                sub_dir,
                subtitle_extensions=subtitle_extensions,
            )

            assert [pair.subtitle.name for pair in pairs] == [expected]

        @pytest.mark.parametrize("reverse", [False, True])
        def test_video_pairing_is_independent_of_directory_order(self, tmp_path, monkeypatch, reverse):
            """Videos whose names collapse onto one episode number must pair
            deterministically: iterdir() enumeration order (filesystem-dependent)
            must never decide which subtitle a video receives."""
            video_dir = tmp_path / "video"
            video_dir.mkdir()
            sub_dir = tmp_path / "subs"
            sub_dir.mkdir()
            # Both stems end in the same bare number, so episode extraction
            # collapses them onto episode 1 and match order decides the pairing.
            videos = [video_dir / "Show Alpha 01.mkv", video_dir / "Show Beta 01.mkv"]
            subtitles = [sub_dir / "Show Alpha 01.srt", sub_dir / "Show Beta 01.srt"]
            for path in [*videos, *subtitles]:
                path.touch()
            ordered = list(reversed(videos)) if reverse else list(videos)

            entries = {video_dir: ordered, sub_dir: subtitles}
            monkeypatch.setattr(Path, "iterdir", lambda path: iter(entries[path]))

            pairs = FilePairMatcher.find_pairs_by_episode_number(video_dir, sub_dir)

            assert [(pair.video.name, pair.subtitle.name) for pair in pairs] == [
                ("Show Alpha 01.mkv", "Show Alpha 01.srt"),
                ("Show Beta 01.mkv", "Show Beta 01.srt"),
            ]

        @pytest.mark.parametrize("reverse", [False, True])
        def test_canonically_equivalent_names_pick_nfc_regardless_of_dir_order(self, tmp_path, monkeypatch, reverse):
            """NFC and NFD spellings share one _nfc key; the raw-name tie-break
            must make selection independent of iterdir() enumeration order."""
            video_dir = tmp_path / "video"
            video_dir.mkdir()
            sub_dir = tmp_path / "subs"
            sub_dir.mkdir()
            video = video_dir / (_DECOMPOSING_STEM + "_01.mkv")
            video.touch()
            names = [
                unicodedata.normalize("NFC", _DECOMPOSING_STEM) + "_01.srt",
                unicodedata.normalize("NFD", _DECOMPOSING_STEM) + "_01.srt",
            ]
            subtitles = [sub_dir / name for name in names]
            for subtitle in subtitles:
                subtitle.touch()
            ordered = list(reversed(subtitles)) if reverse else list(subtitles)

            entries = {video_dir: [video], sub_dir: ordered}
            monkeypatch.setattr(Path, "iterdir", lambda path: iter(entries[path]))

            pairs = FilePairMatcher.find_pairs_by_episode_number(video_dir, sub_dir)

            assert len(pairs) == 1
            # Raw-name key makes the winner order-independent. By codepoint
            # order the NFD spelling wins (base か U+304B < composed が U+304C).
            assert pairs[0].subtitle.name == names[1]


class TestResolveOutputPath:
    """Tests for resolve_output_path — the write-target resolver that stops the
    Windows duplicate-subtitle bug (visually-identical NFC/NFD or case variants)."""

    def test_no_match_returns_desired_path(self, tmp_path):
        """Empty dir → the exact requested path (create-new)."""
        assert resolve_output_path(tmp_path, _NFC_NAME) == tmp_path / _NFC_NAME

    def test_nonexistent_dir_returns_desired_path(self, tmp_path):
        """Unreadable / missing out_dir → desired path, no crash."""
        missing = tmp_path / "does" / "not" / "exist"
        assert resolve_output_path(missing, _NFC_NAME) == missing / _NFC_NAME

    def test_byte_exact_match_returned(self, tmp_path):
        """An existing byte-identical file is the target."""
        (tmp_path / _NFC_NAME).write_text("x")
        assert resolve_output_path(tmp_path, _NFC_NAME) == tmp_path / _NFC_NAME

    def test_nfd_on_disk_nfc_requested_returns_existing_nfd(self, tmp_path):
        """The bug: NFD file on disk + NFC request → resolve to the EXISTING
        NFD path so an overwrite replaces it in place (no twin)."""
        nfd = tmp_path / _NFD_NAME
        nfd.write_text("orig")
        resolved = resolve_output_path(tmp_path, _NFC_NAME)
        assert resolved == nfd
        # And it is the byte-distinct existing file, not a fresh NFC path.
        assert resolved.name.encode("utf-8") == _NFD_NAME.encode("utf-8")

    def test_byte_exact_wins_over_nfd_variant(self, tmp_path):
        """Both an NFC byte-exact and an NFD variant present → byte-exact wins."""
        (tmp_path / _NFD_NAME).write_text("nfd")
        (tmp_path / _NFC_NAME).write_text("nfc")
        assert resolve_output_path(tmp_path, _NFC_NAME) == tmp_path / _NFC_NAME

    def test_suffix_non_collision(self, tmp_path):
        """A same-stem different-extension file must NOT be matched: requesting
        .ass while .srt exists returns the .ass create-new path."""
        (tmp_path / (_DECOMPOSING_STEM + ".srt")).write_text("srt")
        want = _DECOMPOSING_STEM + ".ass"
        assert resolve_output_path(tmp_path, want) == tmp_path / want

    def test_ambiguous_multi_match_refuses_to_guess(self, tmp_path, monkeypatch):
        """≥2 distinct non-byte-exact normalized matches → return desired path
        (create exact bytes), never clobber an arbitrary unrelated file."""
        # Force case-insensitive matching so two case variants collide.
        monkeypatch.setattr(file_pairing, "_CASE_INSENSITIVE_FS", True)
        (tmp_path / "EP01.srt").write_text("a")
        (tmp_path / "eP01.srt").write_text("b")
        # Desired byte-exact ("ep01.srt") is absent; two normalized matches exist.
        assert resolve_output_path(tmp_path, "ep01.srt") == tmp_path / "ep01.srt"

    def test_case_sensitive_fs_does_not_clobber_case_variant(self, tmp_path, monkeypatch):
        """On a case-sensitive FS, a case-only difference is a DISTINCT file:
        requesting EP01.srt while ep01.srt exists must create EP01.srt, not
        overwrite the unrelated ep01.srt (data-loss guard)."""
        monkeypatch.setattr(file_pairing, "_CASE_INSENSITIVE_FS", False)
        (tmp_path / "ep01.srt").write_text("unrelated")
        assert resolve_output_path(tmp_path, "EP01.srt") == tmp_path / "EP01.srt"

    def test_darwin_keeps_requested_case_for_case_distinct_file(self, tmp_path):
        """Darwin may host a case-sensitive volume, so it must not enable
        explicit case folding for output paths."""
        (tmp_path / "ep01.srt").write_text("unrelated")
        code = (
            "import sys;"
            "from pathlib import Path;"
            "sys.platform='darwin';"
            "from anki_miner.utils.file_pairing import resolve_output_path;"
            "print(resolve_output_path(Path(sys.argv[1]), 'EP01.srt'))"
        )

        result = subprocess.run(
            [sys.executable, "-c", code, str(tmp_path)],
            check=True,
            capture_output=True,
            text=True,
        )

        assert result.stdout.strip() == str(tmp_path / "EP01.srt")

    def test_case_insensitive_fs_matches_case_variant(self, tmp_path, monkeypatch):
        """On a case-insensitive FS, a single case variant resolves to it."""
        monkeypatch.setattr(file_pairing, "_CASE_INSENSITIVE_FS", True)
        existing = tmp_path / "ep01.srt"
        existing.write_text("x")
        assert resolve_output_path(tmp_path, "EP01.srt") == existing


def test_find_sibling_subtitle_matches_nfd_stem(tmp_path):
    """find_sibling_subtitle (read path) now NFC-normalizes the stem, so a video
    with an NFC stem finds its NFD-encoded sibling subtitle."""
    from anki_miner.utils.file_pairing import find_sibling_subtitle

    video = tmp_path / (unicodedata.normalize("NFC", _DECOMPOSING_STEM) + ".mkv")
    video.touch()
    sub = tmp_path / _NFD_NAME
    sub.touch()
    assert find_sibling_subtitle(video) == sub


class TestFindSiblingSubtitleIdentity:
    def test_exact_stem_wins_over_earlier_casefold_match(self, monkeypatch):
        from anki_miner.utils.file_pairing import find_sibling_subtitle

        video = Path("/d/Ep.mkv")
        entries = [Path("/d/ep.ass"), Path("/d/Ep.ass")]
        monkeypatch.setattr(Path, "iterdir", lambda _path: iter(entries))
        monkeypatch.setattr(Path, "is_file", lambda _path: True)

        assert find_sibling_subtitle(video) == Path("/d/Ep.ass")

    def test_sole_casefold_match_is_returned(self, monkeypatch):
        from anki_miner.utils.file_pairing import find_sibling_subtitle

        video = Path("/d/Ep.mkv")
        entries = [Path("/d/ep.ass")]
        monkeypatch.setattr(Path, "iterdir", lambda _path: iter(entries))
        monkeypatch.setattr(Path, "is_file", lambda _path: True)

        assert find_sibling_subtitle(video) == Path("/d/ep.ass")

    def test_ambiguous_normalization_only_matches_return_none(self, monkeypatch):
        from anki_miner.utils.file_pairing import find_sibling_subtitle

        video = Path("/d/EP.mkv")
        entries = [Path("/d/ep.ass"), Path("/d/Ep.ass")]
        monkeypatch.setattr(Path, "iterdir", lambda _path: iter(entries))
        monkeypatch.setattr(Path, "is_file", lambda _path: True)

        assert find_sibling_subtitle(video) is None


class TestFindPairsMissingFolder:
    """A nonexistent or non-directory folder yields [] instead of aborting.

    Regression guard: an unhandled FileNotFoundError from iterdir() reaches a Qt
    slot and core-dumps the app (the trailing-space batch bug). The guard also
    covers a folder deleted between selection and processing (TOCTOU).
    """

    def test_nonexistent_video_folder_returns_empty(self, tmp_path):
        missing = tmp_path / "nope"  # never created
        subs = tmp_path / "subs"
        subs.mkdir()
        (subs / "ep01.srt").touch()
        assert FilePairMatcher.find_pairs_by_episode_number(missing, subs) == []

    def test_nonexistent_subtitle_folder_returns_empty(self, tmp_path):
        videos = tmp_path / "videos"
        videos.mkdir()
        (videos / "ep01.mkv").touch()
        missing = tmp_path / "nope"
        assert FilePairMatcher.find_pairs_by_episode_number(videos, missing) == []

    def test_both_missing_return_empty(self, tmp_path):
        assert FilePairMatcher.find_pairs_by_episode_number(tmp_path / "a", tmp_path / "b") == []

    def test_file_passed_as_folder_returns_empty(self, tmp_path):
        """A file path where a folder is expected (NotADirectoryError) also yields []."""
        a_file = tmp_path / "notafolder.txt"
        a_file.write_text("x")
        subs = tmp_path / "subs"
        subs.mkdir()
        assert FilePairMatcher.find_pairs_by_episode_number(a_file, subs) == []


class TestRetimedPreference:
    """Utilities → Retime writes ``<stem>_retimed.<ext>`` beside the original, so
    both files live in one folder. Discovery must reach for the retimed one —
    mining the off-timed original would silently undo the retime — while Retime
    itself opts out (``prefer_retimed=False``) so a rerun never retimes its own
    output.
    """

    @staticmethod
    def _folder(tmp_path: Path, *names: str) -> Path:
        for name in names:
            (tmp_path / name).touch()
        return tmp_path

    def test_retimed_subtitle_wins_the_pairing(self, tmp_path):
        folder = self._folder(tmp_path, "ep01.mkv", "ep01.srt", "ep01_retimed.srt")
        pairs = FilePairMatcher.find_pairs_by_episode_number(folder, folder)
        assert [p.subtitle.name for p in pairs] == ["ep01_retimed.srt"]

    def test_retimed_wins_over_a_higher_priority_extension(self, tmp_path):
        """A retimed .srt beats the .ass it was made from: format priority is
        the tiebreak between equals, not a reason to use the off-timed file."""
        folder = self._folder(tmp_path, "ep01.mkv", "ep01.ass", "ep01_retimed.srt")
        pairs = FilePairMatcher.find_pairs_by_episode_number(folder, folder)
        assert [p.subtitle.name for p in pairs] == ["ep01_retimed.srt"]

    def test_prefer_retimed_false_keeps_the_original(self, tmp_path):
        folder = self._folder(tmp_path, "ep01.mkv", "ep01.srt", "ep01_retimed.srt")
        pairs = FilePairMatcher.find_pairs_by_episode_number(folder, folder, prefer_retimed=False)
        assert [p.subtitle.name for p in pairs] == ["ep01.srt"]

    def test_no_retimed_file_changes_nothing(self, tmp_path):
        folder = self._folder(tmp_path, "ep01.mkv", "ep01.ass", "ep01.srt")
        pairs = FilePairMatcher.find_pairs_by_episode_number(folder, folder)
        assert [p.subtitle.name for p in pairs] == ["ep01.ass"]  # format priority still decides

    def test_sibling_lookup_prefers_the_retimed_file(self, tmp_path):
        from anki_miner.utils.file_pairing import find_sibling_subtitle

        self._folder(tmp_path, "ep01.mkv", "ep01.srt", "ep01_retimed.srt")
        assert find_sibling_subtitle(tmp_path / "ep01.mkv") == tmp_path / "ep01_retimed.srt"

    def test_sibling_lookup_prefers_retimed_across_extensions(self, tmp_path):
        from anki_miner.utils.file_pairing import find_sibling_subtitle

        self._folder(tmp_path, "ep01.mkv", "ep01.ass", "ep01_retimed.srt")
        assert find_sibling_subtitle(tmp_path / "ep01.mkv") == tmp_path / "ep01_retimed.srt"

    def test_sibling_lookup_falls_back_to_the_original(self, tmp_path):
        from anki_miner.utils.file_pairing import find_sibling_subtitle

        self._folder(tmp_path, "ep01.mkv", "ep01.srt")
        assert find_sibling_subtitle(tmp_path / "ep01.mkv") == tmp_path / "ep01.srt"

    def test_ambiguous_retimed_case_twins_return_none(self, monkeypatch):
        """Two retimed candidates matching only after casefolding are ambiguous;
        the read path refuses to let directory order decide (unchanged contract,
        now applied inside the retimed group too)."""
        from anki_miner.utils.file_pairing import find_sibling_subtitle

        video = Path("/d/EP01.mkv")
        entries = [Path("/d/ep01_retimed.ass"), Path("/d/Ep01_retimed.ass")]
        monkeypatch.setattr(Path, "iterdir", lambda _path: iter(entries))
        monkeypatch.setattr(Path, "is_file", lambda _path: True)

        assert find_sibling_subtitle(video) is None
