"""F-04: the translation track is picked by language, then title, then source."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from anki_miner.exceptions import SubtitleParseError
from anki_miner.languages.registry import get_profile
from anki_miner.services.secondary_subtitles import (
    TranslationCandidate,
    load_auto_translation,
    rank_translation_candidates,
    translation_languages,
)
from anki_miner.utils.audio_track_detector import SubtitleStream, TrackTitle

EN = get_profile("en").audio_track_codes
DE = get_profile("de").audio_track_codes
_LIST = "anki_miner.services.secondary_subtitles.list_subtitle_streams"
_EXTRACT = "anki_miner.services.audio_condenser.AudioCondenserService.extract_embedded_subtitle"
CUES = [(1.0, 2.0, "Hello.")]


def _stream(sub_index: int, lang: str | None, title: str | None = None, *, text: bool = True) -> SubtitleStream:
    return SubtitleStream(
        index=sub_index + 1,
        sub_index=sub_index,
        codec_name="subrip" if text else "hdmv_pgs_subtitle",
        language_tag=lang,
        title=title,
        is_text=text,
    )


def _parser(entries=CUES) -> MagicMock:
    parser = MagicMock()
    parser.parse_raw_entries.return_value = list(entries)
    return parser


def _fake_extract(extracted: Path):
    def _extract(self, video, stream, out_dir, cancel_event=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        extracted.write_text("x", encoding="utf-8")
        return extracted

    return _extract


def test_an_english_interface_asks_for_english_only():
    assert translation_languages("en") == (EN,)


def test_a_german_interface_asks_for_german_then_english():
    assert translation_languages("de") == (DE, EN)


def test_a_regional_interface_code_uses_its_language():
    assert translation_languages("pt_br") == (get_profile("pt").audio_track_codes, EN)


def test_ranking_puts_language_before_title():
    english_full = TranslationCandidate(1, TrackTitle.FULL, sidecar=Path("ep.en.full.srt"))
    german_plain = TranslationCandidate(0, TrackTitle.PLAIN, sidecar=Path("ep.de.srt"))
    assert rank_translation_candidates([english_full, german_plain]) == [german_plain, english_full]


def test_signs_tracks_are_dropped_and_sdh_ranks_below_plain():
    signs = TranslationCandidate(0, TrackTitle.PARTIAL, stream=_stream(0, "eng", "Signs"))
    sdh = TranslationCandidate(0, TrackTitle.ANNOTATED, stream=_stream(1, "eng", "SDH"))
    plain = TranslationCandidate(0, TrackTitle.PLAIN, stream=_stream(2, "eng"))
    assert rank_translation_candidates([signs, sdh, plain]) == [plain, sdh]


def test_a_sidecar_beats_an_embedded_stream_of_the_same_rank():
    embedded = TranslationCandidate(0, TrackTitle.PLAIN, stream=_stream(0, "eng"))
    sidecar = TranslationCandidate(0, TrackTitle.PLAIN, sidecar=Path("ep.en.srt"))
    assert rank_translation_candidates([embedded, sidecar]) == [sidecar, embedded]


def test_labels_name_the_file_or_the_track():
    assert TranslationCandidate(0, TrackTitle.PLAIN, sidecar=Path("/x/ep.en.srt")).label == "ep.en.srt"
    assert TranslationCandidate(0, TrackTitle.FULL, stream=_stream(1, "eng", "Full")).label == "2 [eng] Full"
    assert TranslationCandidate(0, TrackTitle.PLAIN, stream=_stream(0, "eng")).label == "1 [eng]"


def test_a_sidecar_is_read_at_zero_offset_without_the_mining_ladder(test_config, tmp_path):
    video = tmp_path / "ep01.mkv"
    video.write_bytes(b"")
    (tmp_path / "ep01.en.srt").write_text("x", encoding="utf-8")
    parser = _parser()
    with patch(_LIST, return_value=[]):
        picked = load_auto_translation(video, test_config, parser, tmp_path / "tmp")
    assert picked is not None and picked.entries == CUES
    assert picked.candidate.label == "ep01.en.srt"
    parser.parse_raw_entries.assert_called_once_with(tmp_path / "ep01.en.srt", 0.0, encodings=())


def test_an_embedded_english_stream_is_extracted_parsed_and_deleted(test_config, tmp_path):
    video = tmp_path / "ep01.mkv"
    video.write_bytes(b"")
    extracted = tmp_path / "tmp" / "ep01.s1.x.srt"
    streams = [_stream(0, "jpn"), _stream(1, "eng", "Full Subtitles"), _stream(2, "eng", "Signs & Songs")]
    with (
        patch(_LIST, return_value=streams),
        patch(_EXTRACT, autospec=True, side_effect=_fake_extract(extracted)) as extract,
    ):
        picked = load_auto_translation(video, test_config, _parser(), tmp_path / "tmp")
    assert picked is not None and picked.candidate.stream is streams[1]
    assert picked.candidate.label == "2 [eng] Full Subtitles"
    assert extract.call_args.args[2] is streams[1]
    assert not extracted.exists()


def test_untagged_image_and_other_language_streams_are_never_picked(test_config, tmp_path):
    video = tmp_path / "ep01.mkv"
    video.write_bytes(b"")
    streams = [_stream(0, "jpn"), _stream(1, None), _stream(2, "eng", text=False), _stream(3, "fre")]
    with patch(_LIST, return_value=streams), patch(_EXTRACT) as extract:
        assert load_auto_translation(video, test_config, _parser(), tmp_path / "tmp") is None
    extract.assert_not_called()


def test_a_candidate_that_does_not_parse_falls_through_to_the_next(test_config, tmp_path):
    video = tmp_path / "ep01.mkv"
    video.write_bytes(b"")
    (tmp_path / "ep01.en.srt").write_text("x", encoding="utf-8")
    (tmp_path / "ep01.en.sdh.srt").write_text("x", encoding="utf-8")
    parser = MagicMock()
    parser.parse_raw_entries.side_effect = [SubtitleParseError("garbage"), list(CUES)]
    with patch(_LIST, return_value=[]):
        picked = load_auto_translation(video, test_config, parser, tmp_path / "tmp")
    assert picked is not None and picked.candidate.label == "ep01.en.sdh.srt"


def test_an_empty_parse_falls_through_too(test_config, tmp_path):
    video = tmp_path / "ep01.mkv"
    video.write_bytes(b"")
    (tmp_path / "ep01.en.srt").write_text("x", encoding="utf-8")
    with patch(_LIST, return_value=[]):
        assert load_auto_translation(video, test_config, _parser([]), tmp_path / "tmp") is None


def test_spanish_mining_with_a_german_interface_picks_the_german_track(test_config, tmp_path):
    # Review Focus 3: a non-Japanese mining language; the Spanish track is the mining one.
    config = replace(test_config, language="es", ui_language="de")
    video = tmp_path / "cap01.mkv"
    video.write_bytes(b"")
    streams = [_stream(0, "spa"), _stream(1, "eng"), _stream(2, "ger")]
    with (
        patch(_LIST, return_value=streams),
        patch(_EXTRACT, autospec=True, side_effect=_fake_extract(tmp_path / "tmp" / "cap01.s2.srt")),
    ):
        picked = load_auto_translation(video, config, _parser(), tmp_path / "tmp")
    assert picked is not None and picked.candidate.stream is streams[2]


def test_the_mining_track_is_never_its_own_translation(test_config, tmp_path):
    # English mining with an English interface: the English text track is the mining track, not a translation.
    config = replace(test_config, language="en", ui_language="en")
    video = tmp_path / "ep01.mkv"
    video.write_bytes(b"")
    with patch(_LIST, return_value=[_stream(0, "eng", "Full")]), patch(_EXTRACT, autospec=True) as extract:
        assert load_auto_translation(video, config, _parser(), tmp_path / "tmp") is None
    extract.assert_not_called()
