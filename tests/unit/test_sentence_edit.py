"""Curator sentence edits: the intent model and the one resolver that reads it."""

from __future__ import annotations

import dataclasses
import logging
import unicodedata

import pytest

from anki_miner.models import SentenceEdit, TokenizedWord
from anki_miner.services.sentence_edit import nearest_token, pick_target, resolve_sentence_edit


def _word(**kwargs) -> TokenizedWord:
    base = {
        "surface": "時給",
        "lemma": "時給",
        "reading": "ジキュウ",
        "sentence": "時給系のスポーツは本当に苦手",
        "start_time": 5.0,
        "end_time": 7.0,
        "duration": 2.0,
        "pos": "名詞",
        "surface_start": 0,
        "surface_end": 2,
        "mined_form_override": "時給",
    }
    base.update(kwargs)
    return TokenizedWord(**base)


class TestModel:
    def test_default_is_untouched(self):
        assert _word().sentence_edit is None

    def test_edit_is_frozen(self):
        edit = SentenceEdit(text="持久系のスポーツは本当に苦手", target_start=0, target_end=2)
        with pytest.raises(dataclasses.FrozenInstanceError):
            edit.text = "x"  # type: ignore[misc]

    def test_replace_carries_the_edit(self):
        edit = SentenceEdit(text="持久系のスポーツは本当に苦手", target_start=0, target_end=2)
        assert dataclasses.replace(_word(), sentence_edit=edit).sentence_edit == edit


# ---------------------------------------------------------------------------
# services.sentence_edit — the one resolver
# ---------------------------------------------------------------------------


EDITED = "持久系のスポーツは本当に苦手"


def _token(surface: str, start: int, *, mined: str | None = None, reading: str = "", sentence: str = EDITED, **kw):
    """A word as parse_text_units would emit it for ``sentence``."""
    return TokenizedWord(
        surface=surface,
        lemma=mined or surface,
        reading=reading,
        sentence=sentence,
        start_time=0.0,
        end_time=0.0,
        duration=0.0,
        pos="名詞",
        surface_start=start,
        surface_end=start + len(surface),
        highlight_end=-1,
        expression_reading=reading,
        sentence_bolded=f"<b>{surface}</b>" if start == 0 else "",
        mined_form_override=mined or surface,
        **kw,
    )


PARSED = [_token("持久", 0, reading="じきゅう"), _token("スポーツ", 4), _token("本当", 10), _token("苦手", 13)]


class TestPickTarget:
    def test_exact_span_wins(self):
        assert pick_target(PARSED, 4, 8) is PARSED[1]

    def test_overlap_falls_back_to_first_overlapping_token(self):
        # A span that straddles 本当 and 苦手 resolves to the earlier one.
        assert pick_target(PARSED, 11, 14) is PARSED[2]

    def test_no_overlap_is_none(self):
        assert pick_target(PARSED, 8, 10) is None

    def test_untracked_offsets_never_match(self):
        untracked = [_token("持久", -1)]
        assert pick_target(untracked, 0, 2) is None


class TestNearestToken:
    def test_nearest_start_wins(self):
        assert nearest_token(PARSED, 9) is PARSED[2]

    def test_tie_goes_to_the_earlier_token(self):
        assert nearest_token(PARSED, 2) is PARSED[0]  # 0 and 4 are both 2 away

    def test_empty_is_none(self):
        assert nearest_token([], 0) is None


class TestResolveSentenceEdit:
    def _intent(self, **kw):
        return _word(
            sentence_edit=SentenceEdit(text=EDITED, target_start=0, target_end=2),
            clip_override=(4.0, 7.5),
            screenshot_override=6.2,
            video_file=None,
            frequency_rank=1234,
            **kw,
        )

    def test_untouched_word_is_returned_as_is(self):
        word = _word()
        assert resolve_sentence_edit(word, lambda text: PARSED) is word

    def test_rebuilt_word_is_the_parsed_token(self):
        parsed_texts: list[str] = []

        def parse(text):
            parsed_texts.append(text)
            return PARSED

        rebuilt = resolve_sentence_edit(self._intent(), parse)

        assert parsed_texts == [EDITED]
        assert rebuilt.mined_form == "持久"
        assert rebuilt.expression_reading == "じきゅう"
        assert rebuilt.sentence == EDITED
        assert rebuilt.sentence[rebuilt.surface_start : rebuilt.surface_end] == rebuilt.surface
        assert rebuilt.sentence_bolded == "<b>持久</b>"

    def test_timing_media_and_overrides_come_from_the_original(self):
        rebuilt = resolve_sentence_edit(self._intent(), lambda text: PARSED)
        assert (rebuilt.start_time, rebuilt.end_time, rebuilt.duration) == (5.0, 7.0, 2.0)
        assert rebuilt.clip_override == (4.0, 7.5)
        assert rebuilt.screenshot_override == 6.2
        assert rebuilt.line_expansion == (0, 0)

    def test_intent_is_absorbed_and_stale_derived_fields_dropped(self):
        rebuilt = resolve_sentence_edit(self._intent(), lambda text: PARSED)
        assert rebuilt.sentence_edit is None
        assert rebuilt.sentence_candidates == []
        # The processor re-ranks the new word; the old rank must not survive.
        assert rebuilt.frequency_rank is None
        assert rebuilt.frequency_sources == []
        assert rebuilt.frequency_harmonic_rank is None

    def test_missing_span_keeps_the_original_and_warns(self, caplog):
        word = self._intent()
        with caplog.at_level(logging.WARNING, logger="anki_miner.services.sentence_edit"):
            result = resolve_sentence_edit(word, lambda text: [])
        assert result.sentence == word.sentence
        assert result.mined_form == word.mined_form
        assert result.sentence_edit is None
        assert "sentence edit" in caplog.text

    def test_source_word_is_not_mutated(self):
        word = self._intent()
        resolve_sentence_edit(word, lambda text: PARSED)
        assert word.sentence_edit is not None
        assert word.sentence == "時給系のスポーツは本当に苦手"


# ---------------------------------------------------------------------------
# Real parser round trip
# ---------------------------------------------------------------------------


def _fugashi_available() -> bool:
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
def test_real_parser_round_trip_rebuilds_the_corrected_word(tmp_path):
    """The user's own example: 時給系 mistranscribed for 持久系. The processor's
    parse_sentence_fn and the resolver, over the real ja parser."""
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.services.subtitle_parser import SubtitleParserService
    from tests.conftest import build_processor

    config = AnkiMinerConfig(media_temp_folder=tmp_path / "media", bold_target_in_sentence=True)
    proc = build_processor(config, subtitle_parser=SubtitleParserService(config))
    edited = "持久系のスポーツは本当に苦手"

    tokens = proc.parse_sentence_fn(edited)

    target = next(t for t in tokens if t.mined_form.startswith("持久"))
    assert target.sentence == edited
    assert target.sentence[target.surface_start : target.surface_end] == target.surface
    assert target.expression_reading.startswith("じきゅう")

    original = _word()
    intent = dataclasses.replace(
        original,
        sentence_edit=SentenceEdit(text=edited, target_start=target.surface_start, target_end=target.surface_end),
    )
    rebuilt = resolve_sentence_edit(intent, proc.parse_sentence_fn)

    assert rebuilt.mined_form == target.mined_form
    assert rebuilt.sentence == edited
    assert (rebuilt.start_time, rebuilt.end_time) == (5.0, 7.0)
    assert "<b>" in rebuilt.sentence_bolded  # bold precompute is config-gated; enabled above


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
def test_real_parser_round_trip_survives_width_and_nfc_normalisation(tmp_path):
    """The curator stores the parser's own ``token.sentence`` (folded to
    fullwidth + NFC), so the resolver's re-parse changes no text and the stored
    span lands on the same token. Pinned with text the folding changes in both
    width and code-point count."""
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.services.subtitle_parser import SubtitleParserService
    from tests.conftest import build_processor

    config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
    proc = build_processor(config, subtitle_parser=SubtitleParserService(config))
    # Halfwidth katakana plus a decomposed が (か + U+3099): both change under normalisation.
    typed = "ﾀﾅｶさんは持久系のスポーツ" + unicodedata.normalize("NFD", "が") + "苦手"
    assert typed != unicodedata.normalize("NFC", typed)

    tokens = proc.parse_sentence_fn(typed)
    target = next(t for t in tokens if t.mined_form.startswith("持久"))
    stored = target.sentence  # what SentenceEditDialog hands the curator
    assert stored == "タナカさんは持久系のスポーツが苦手"
    assert stored[target.surface_start : target.surface_end] == target.surface

    intent = dataclasses.replace(
        _word(),
        sentence_edit=SentenceEdit(text=stored, target_start=target.surface_start, target_end=target.surface_end),
    )
    rebuilt = resolve_sentence_edit(intent, proc.parse_sentence_fn)

    assert rebuilt.mined_form == target.mined_form
    assert rebuilt.sentence == stored
    assert rebuilt.sentence[rebuilt.surface_start : rebuilt.surface_end] == rebuilt.surface
