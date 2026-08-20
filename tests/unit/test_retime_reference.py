"""Tests for retiming reference selection, cleaning, and the audio fallback.

The behaviour under test is "what does alass align against, and why" — the
subtitle-first policy, the three layers of signs-track rejection, and the
guarantee that no failure mode blocks a run.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pysubs2
import pytest

from anki_miner.services.retime_reference import (
    ReferenceOverride,
    _clean_reference,
    list_reference_subtitle_streams,
    resolve_reference,
)
from anki_miner.utils.audio_track_detector import SubtitleStream

_LIST_STREAMS = "anki_miner.services.retime_reference.list_subtitle_streams"
_RESOLVE_FFPROBE = "anki_miner.services.retime_reference.resolve_ffprobe"
# Both extraction seams are lazily imported inside the resolver, so the patch
# has to land on the defining module, not on a name bound in retime_reference.
_CONDENSER = "anki_miner.services.audio_condenser.AudioCondenserService"
_EXTRACTOR = "anki_miner.services.media_extractor.MediaExtractorService"


def _stream(
    sub_index: int,
    *,
    language: str | None = "eng",
    title: str | None = None,
    is_text: bool = True,
    is_forced: bool = False,
    is_default: bool = False,
) -> SubtitleStream:
    return SubtitleStream(
        index=sub_index + 1,
        sub_index=sub_index,
        codec_name="ass" if is_text else "hdmv_pgs_subtitle",
        language_tag=language,
        title=title,
        is_text=is_text,
        is_forced=is_forced,
        is_default=is_default,
    )


def _dialogue_srt(path: Path, cues: int) -> Path:
    """Write an SRT with *cues* one-second cues, one per second."""
    path.write_text(
        "".join(f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},500\nline {i}\n\n" for i in range(1, cues + 1)),
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def config(tmp_path: Path) -> MagicMock:
    cfg = MagicMock()
    cfg.media_temp_folder = tmp_path / "temp"
    return cfg


@pytest.fixture()
def video(tmp_path: Path) -> Path:
    p = tmp_path / "ep01.mkv"
    p.touch()
    return p


# ---------------------------------------------------------------------------
# Candidate ordering and filtering
# ---------------------------------------------------------------------------


class TestCandidateOrdering:
    def test_japanese_beats_english_beats_other(self, config: MagicMock, video: Path) -> None:
        streams = [_stream(0, language="ger"), _stream(1, language="eng"), _stream(2, language="jpn")]
        with patch(_LIST_STREAMS, return_value=streams), patch(_RESOLVE_FFPROBE, return_value="ffprobe"):
            ordered = list_reference_subtitle_streams(config, video)
        assert [s.language_tag for s in ordered] == ["jpn", "eng", "ger"]

    def test_dialogue_outranks_signs_regardless_of_language(self, config: MagicMock, video: Path) -> None:
        """A Japanese signs track must not beat an English dialogue track."""
        streams = [_stream(0, language="jpn", title="Signs & Songs"), _stream(1, language="eng")]
        with patch(_LIST_STREAMS, return_value=streams), patch(_RESOLVE_FFPROBE, return_value="ffprobe"):
            ordered = list_reference_subtitle_streams(config, video)
        assert [s.sub_index for s in ordered] == [1, 0]

    def test_bitmap_streams_are_not_candidates(self, config: MagicMock, video: Path) -> None:
        streams = [_stream(0, is_text=False), _stream(1)]
        with patch(_LIST_STREAMS, return_value=streams), patch(_RESOLVE_FFPROBE, return_value="ffprobe"):
            ordered = list_reference_subtitle_streams(config, video)
        assert [s.sub_index for s in ordered] == [1]

    def test_default_disposition_breaks_a_tie(self, config: MagicMock, video: Path) -> None:
        streams = [_stream(0, language="eng"), _stream(1, language="eng", is_default=True)]
        with patch(_LIST_STREAMS, return_value=streams), patch(_RESOLVE_FFPROBE, return_value="ffprobe"):
            ordered = list_reference_subtitle_streams(config, video)
        assert [s.sub_index for s in ordered] == [1, 0]


# ---------------------------------------------------------------------------
# Reference cleaning
# ---------------------------------------------------------------------------


class TestCleanReference:
    def _clean(self, tmp_path: Path, events: list[pysubs2.SSAEvent]) -> pysubs2.SSAFile:
        src = tmp_path / "in.ass"
        subs = pysubs2.SSAFile()
        subs.events = events
        subs.save(str(src), encoding="utf-8")
        dest = tmp_path / "out.srt"
        _clean_reference(src, dest)
        return pysubs2.load(str(dest))

    def test_comments_dropped(self, tmp_path: Path) -> None:
        comment = pysubs2.SSAEvent(start=0, end=1000, text="staff credit")
        comment.is_comment = True
        out = self._clean(tmp_path, [comment, pysubs2.SSAEvent(start=2000, end=3000, text="hi")])
        assert [e.plaintext for e in out.events] == ["hi"]

    def test_sign_and_song_styles_dropped(self, tmp_path: Path) -> None:
        out = self._clean(
            tmp_path,
            [
                pysubs2.SSAEvent(start=0, end=1000, text="shop", style="Signs"),
                pysubs2.SSAEvent(start=2000, end=3000, text="la la", style="OP-Romaji"),
                pysubs2.SSAEvent(start=4000, end=5000, text="talk", style="Default"),
            ],
        )
        assert [e.plaintext for e in out.events] == ["talk"]

    def test_a_style_named_subtitle_is_not_a_title_style(self, tmp_path: Path) -> None:
        """The style filter is word-bounded; ``Subtitle`` must not match ``title``."""
        out = self._clean(tmp_path, [pysubs2.SSAEvent(start=0, end=1000, text="talk", style="Subtitle")])
        assert [e.plaintext for e in out.events] == ["talk"]

    def test_drawings_and_empty_spans_dropped(self, tmp_path: Path) -> None:
        out = self._clean(
            tmp_path,
            [
                pysubs2.SSAEvent(start=0, end=1000, text="{\\p1}m 0 0 l 10 10{\\p0}"),
                pysubs2.SSAEvent(start=2000, end=2000, text="zero length"),
                pysubs2.SSAEvent(start=4000, end=3000, text="negative"),
                pysubs2.SSAEvent(start=5000, end=6000, text="talk"),
            ],
        )
        assert [e.plaintext for e in out.events] == ["talk"]

    def test_duplicate_spans_collapsed(self, tmp_path: Path) -> None:
        out = self._clean(
            tmp_path,
            [
                pysubs2.SSAEvent(start=0, end=1000, text="a"),
                pysubs2.SSAEvent(start=0, end=1000, text="a (dup track)"),
                pysubs2.SSAEvent(start=2000, end=3000, text="b"),
            ],
        )
        assert len(out.events) == 2

    def test_returns_the_written_cue_count(self, tmp_path: Path) -> None:
        src = _dialogue_srt(tmp_path / "in.srt", 5)
        assert _clean_reference(src, tmp_path / "out.srt").cues == 5

    def test_cp932_source_is_readable(self, tmp_path: Path) -> None:
        """Japanese embedded tracks are routinely Shift-JIS, not UTF-8."""
        src = tmp_path / "in.srt"
        src.write_bytes("1\n00:00:01,000 --> 00:00:02,000\nこんにちは\n\n".encode("cp932"))
        assert _clean_reference(src, tmp_path / "out.srt").cues == 1


# ---------------------------------------------------------------------------
# resolve_reference
# ---------------------------------------------------------------------------


class TestResolveReference:
    """Auto-selection, override handling, and the never-block guarantee.

    There are two extraction seams: subtitle extraction reused from the
    condenser and audio extraction from the media extractor. Several tests need
    to drive the two independently, so both are patched explicitly.
    """

    def test_prefers_a_usable_subtitle_track(self, config: MagicMock, video: Path, tmp_path: Path) -> None:
        extracted = _dialogue_srt(tmp_path / "embedded.srt", 40)
        with (
            patch(_LIST_STREAMS, return_value=[_stream(0)]),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_CONDENSER) as condenser,
            patch(_EXTRACTOR) as extractor,
        ):
            condenser.return_value.extract_embedded_subtitle.return_value = extracted
            reference = resolve_reference(config, video)

        assert reference is not None
        assert reference.kind == "subtitle"
        assert reference.path.name.endswith(".clean.srt")
        extractor.return_value.extract_full_audio.assert_not_called()

    def test_sparse_track_is_rejected_and_the_next_one_tried(
        self, config: MagicMock, video: Path, tmp_path: Path
    ) -> None:
        """A signs track that lies in its title is caught by the cue-count floor."""
        sparse = _dialogue_srt(tmp_path / "sparse.srt", 5)
        dense = _dialogue_srt(tmp_path / "dense.srt", 40)
        with (
            patch(_LIST_STREAMS, return_value=[_stream(0, language="jpn"), _stream(1, language="eng")]),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_CONDENSER) as condenser,
        ):
            condenser.return_value.extract_embedded_subtitle.side_effect = [sparse, dense]
            reference = resolve_reference(config, video)

        assert reference is not None
        assert reference.kind == "subtitle"
        assert condenser.return_value.extract_embedded_subtitle.call_count == 2

    def test_low_coverage_track_rejected_when_duration_known(
        self, config: MagicMock, video: Path, tmp_path: Path
    ) -> None:
        """An untitled recap/signs track passing the cue floor still fails the
        coverage gate: 40 cues spanning ~40s of a 2-minute episode."""
        clustered = _dialogue_srt(tmp_path / "clustered.srt", 40)
        with (
            patch(_LIST_STREAMS, return_value=[_stream(0)]),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_CONDENSER) as condenser,
            patch(_EXTRACTOR) as extractor,
        ):
            condenser.return_value.extract_embedded_subtitle.return_value = clustered
            extractor.return_value.extract_full_audio.return_value = False
            reference = resolve_reference(config, video, video_duration_seconds=120.0)

        assert reference is None  # fell through to audio, which also failed

    def test_low_coverage_track_accepted_under_explicit_override(
        self, config: MagicMock, video: Path, tmp_path: Path
    ) -> None:
        """The auto-pick path rejects this exact track
        (``test_low_coverage_track_rejected_when_duration_known`` above: 40
        cues spanning ~40s of a 2-minute episode). An explicit override must
        accept it anyway -- the coverage gate is skipped entirely on that
        path, not just relaxed, because the user outranks the heuristic."""
        clustered = _dialogue_srt(tmp_path / "clustered.srt", 40)
        with (
            patch(_LIST_STREAMS, return_value=[_stream(0)]),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_CONDENSER) as condenser,
        ):
            condenser.return_value.extract_embedded_subtitle.return_value = clustered
            reference = resolve_reference(
                config,
                video,
                override=ReferenceOverride(kind="subtitle", index=0),
                video_duration_seconds=120.0,
            )

        assert reference is not None
        assert reference.kind == "subtitle"

    def test_coverage_gate_passes_a_full_episode_track(self, config: MagicMock, video: Path, tmp_path: Path) -> None:
        dense = _dialogue_srt(tmp_path / "dense.srt", 40)  # spans ~40s
        with (
            patch(_LIST_STREAMS, return_value=[_stream(0)]),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_CONDENSER) as condenser,
        ):
            condenser.return_value.extract_embedded_subtitle.return_value = dense
            reference = resolve_reference(config, video, video_duration_seconds=60.0)

        assert reference is not None
        assert reference.kind == "subtitle"

    def test_audio_fallback_labels_missing_japanese_tag_honestly(self, config: MagicMock, video: Path) -> None:
        with (
            patch(_LIST_STREAMS, return_value=[]),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_EXTRACTOR) as extractor,
            patch(
                "anki_miner.services.retime_reference.find_japanese_audio_stream",
                return_value=None,
            ),
        ):
            extractor.return_value.extract_full_audio.return_value = True
            lines: list[str] = []
            reference = resolve_reference(config, video, log_cb=lines.append)

        assert reference is not None
        assert reference.label == "first audio track (no Japanese tag)"
        assert any("may be a dub" in line for line in lines)
        reference.path.unlink()

    def test_audio_fallback_labels_japanese_track(self, config: MagicMock, video: Path) -> None:
        with (
            patch(_LIST_STREAMS, return_value=[]),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_EXTRACTOR) as extractor,
            patch(
                "anki_miner.services.retime_reference.find_japanese_audio_stream",
                return_value=MagicMock(),
            ),
        ):
            extractor.return_value.extract_full_audio.return_value = True
            reference = resolve_reference(config, video)

        assert reference is not None
        assert reference.label == "Japanese audio"
        reference.path.unlink()

    def test_forced_track_is_skipped_without_extraction(self, config: MagicMock, video: Path) -> None:
        with (
            patch(_LIST_STREAMS, return_value=[_stream(0, is_forced=True)]),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_CONDENSER) as condenser,
            patch(_EXTRACTOR) as extractor,
        ):
            extractor.return_value.extract_full_audio.return_value = False
            resolve_reference(config, video)

        condenser.return_value.extract_embedded_subtitle.assert_not_called()

    def test_falls_back_to_audio_with_no_subtitle_tracks(self, config: MagicMock, video: Path) -> None:
        with (
            patch(_LIST_STREAMS, return_value=[]),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_EXTRACTOR) as extractor,
        ):
            extractor.return_value.extract_full_audio.return_value = True
            reference = resolve_reference(config, video)

        assert reference is not None
        assert reference.kind == "audio"
        assert reference.path.name.endswith(".retime-ref.wav")
        reference.path.unlink()

    def test_returns_none_when_audio_extraction_also_fails(self, config: MagicMock, video: Path) -> None:
        """The caller then hands alass the raw video - nothing blocks the run."""
        with (
            patch(_LIST_STREAMS, return_value=[]),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_EXTRACTOR) as extractor,
        ):
            extractor.return_value.extract_full_audio.return_value = False
            assert resolve_reference(config, video) is None

    def test_audio_override_skips_subtitle_probing(self, config: MagicMock, video: Path) -> None:
        with (
            patch(_LIST_STREAMS) as list_streams,
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_EXTRACTOR) as extractor,
        ):
            extractor.return_value.extract_full_audio.return_value = True
            reference = resolve_reference(config, video, override=ReferenceOverride(kind="audio", index=2))

        list_streams.assert_not_called()
        assert reference is not None and reference.kind == "audio"
        _, kwargs = extractor.return_value.extract_full_audio.call_args
        assert kwargs["track_override"] == 2
        reference.path.unlink()

    def test_subtitle_override_picks_that_track(self, config: MagicMock, video: Path, tmp_path: Path) -> None:
        """An explicit pick wins even over a better-ranked candidate."""
        extracted = _dialogue_srt(tmp_path / "embedded.srt", 40)
        streams = [_stream(0, language="jpn"), _stream(1, language="eng")]
        with (
            patch(_LIST_STREAMS, return_value=streams),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_CONDENSER) as condenser,
        ):
            condenser.return_value.extract_embedded_subtitle.return_value = extracted
            reference = resolve_reference(config, video, override=ReferenceOverride(kind="subtitle", index=1))

        assert reference is not None and reference.kind == "subtitle"
        args, _ = condenser.return_value.extract_embedded_subtitle.call_args
        assert args[1].sub_index == 1

    def test_unusable_subtitle_override_degrades_to_audio(self, config: MagicMock, video: Path) -> None:
        """Refusing to run would be worse than quietly switching, so it switches."""
        logged: list[str] = []
        with (
            patch(_LIST_STREAMS, return_value=[_stream(0)]),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_CONDENSER) as condenser,
            patch(_EXTRACTOR) as extractor,
        ):
            condenser.return_value.extract_embedded_subtitle.return_value = None
            extractor.return_value.extract_full_audio.return_value = True
            reference = resolve_reference(
                config,
                video,
                override=ReferenceOverride(kind="subtitle", index=0),
                log_cb=logged.append,
            )

        assert reference is not None and reference.kind == "audio"
        assert any("unusable" in line for line in logged)
        reference.path.unlink()

    def test_extraction_failure_is_swallowed(self, config: MagicMock, video: Path) -> None:
        """An ffmpeg blow-up must degrade, not propagate."""
        with (
            patch(_LIST_STREAMS, return_value=[_stream(0)]),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_CONDENSER) as condenser,
            patch(_EXTRACTOR) as extractor,
        ):
            condenser.return_value.extract_embedded_subtitle.side_effect = OSError("boom")
            extractor.return_value.extract_full_audio.return_value = False
            assert resolve_reference(config, video) is None

    def test_rejected_candidate_leaves_no_temp_behind(self, config: MagicMock, video: Path, tmp_path: Path) -> None:
        sparse = _dialogue_srt(tmp_path / "sparse.srt", 5)
        with (
            patch(_LIST_STREAMS, return_value=[_stream(0)]),
            patch(_RESOLVE_FFPROBE, return_value="ffprobe"),
            patch(_CONDENSER) as condenser,
            patch(_EXTRACTOR) as extractor,
        ):
            condenser.return_value.extract_embedded_subtitle.return_value = sparse
            extractor.return_value.extract_full_audio.return_value = False
            resolve_reference(config, video)

        assert not sparse.exists()
        assert not sparse.with_suffix(".clean.srt").exists()
