"""Tests for file_pairing module."""

import unicodedata

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

        def test_pairs_sorted_by_episode_ascending(self, tmp_path):
            """Preview consumes this order, so it must be ascending by episode
            number regardless of filesystem iteration order (Issue #80)."""
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
