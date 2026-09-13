"""S13: a language with no sentence annotator gets no fabricated sentence reading."""

from __future__ import annotations

import dataclasses

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages import tagger_provider
from anki_miner.languages.registry import get_profile
from anki_miner.models.reading import ReadingUnit
from tests.unit.languages.eu_stub import WhitespaceTagger

UNITS = [ReadingUnit(text="The cat sat.", index=0, location_label="p.1")]


def test_default_keeps_the_generated_sentence_fields(make_eu_parser):
    words, index, _counts = make_eu_parser().parse_text_units(UNITS, want_line_index=True)
    assert words[0].sentence_reading == "Thecatsat."  # the pre-S13 contiguous-token output
    assert index[0].sentence_reading == "Thecatsat."


def test_annotation_off_leaves_the_three_fields_empty(make_eu_parser, stub_eu_config):
    parser = make_eu_parser(sentence_annotation=False)
    parser.config = dataclasses.replace(stub_eu_config, bold_target_in_sentence=True)

    words, index, _counts = parser.parse_text_units(UNITS, want_line_index=True)

    assert {(w.sentence_furigana, w.sentence_reading, w.sentence_furigana_bolded) for w in words} == {("", "", "")}
    assert (index[0].sentence_furigana, index[0].sentence_reading) == ("", "")
    assert all(w.sentence_bolded for w in words)  # the plain bolded sentence is not an annotation


def test_ko_and_zh_factories_turn_annotation_off(monkeypatch):
    for code in ("ko", "zh"):
        monkeypatch.setitem(tagger_provider._TAGGERS, code, WhitespaceTagger())
        profile = get_profile(code)
        parser = profile.create_parser(dataclasses.replace(AnkiMinerConfig(), language=code))
        assert profile.sentence_annotator is None
        assert parser._sentence_annotation is False


def test_japanese_keeps_annotation(test_config):
    from anki_miner.services.subtitle_parser import SubtitleParserService

    assert SubtitleParserService(test_config)._sentence_annotation is True


def test_a_korean_line_expansion_leaves_the_annotation_fields_empty(test_config):
    """WordFilterService regenerates the fields after a curator expansion; the gate covers it too."""
    from anki_miner.models import TokenizedWord
    from anki_miner.services.word_filter import WordFilterService

    entries = [(0.0, 1.0, "학교에 갔어요."), (1.0, 2.0, "친구를 만났어요.")]
    word = TokenizedWord(
        surface="친구",
        lemma="친구",
        reading="",
        sentence="친구를 만났어요.",
        start_time=1.0,
        end_time=2.0,
        duration=1.0,
        surface_start=0,
        surface_end=2,
        line_expansion=(1, 0),
        sentence_reading="친구를만났어요.",
    )
    config = dataclasses.replace(test_config, bold_target_in_sentence=True)
    tagger = WhitespaceTagger()

    annotated = WordFilterService(config, tagger=tagger).expand_word_lines(word, entries)
    gated = WordFilterService(config, tagger=tagger, sentence_annotation=False).expand_word_lines(word, entries)

    assert annotated.sentence_reading  # the pre-gate regeneration still runs for ja
    assert (gated.sentence_furigana, gated.sentence_reading, gated.sentence_furigana_bolded) == ("", "", "")
    assert gated.sentence_bolded  # the plain bolded sentence is not an annotation


def test_both_composition_roots_pass_the_annotator_gate(test_config, monkeypatch):
    import inspect
    from types import SimpleNamespace

    from anki_miner.gui.utils import service_factory
    from anki_miner.gui.workers import deck_filter_worker

    profile = SimpleNamespace(
        mined_form=SimpleNamespace(), script=SimpleNamespace(), dedup_fold=None, sentence_annotator=None
    )
    monkeypatch.setattr(deck_filter_worker, "get_profile", lambda code: profile)
    monkeypatch.setattr(deck_filter_worker, "KnownWordDB", lambda *a, **k: None)
    monkeypatch.setattr("anki_miner.services.tagger.get_shared_tagger", lambda: None)

    bundle = deck_filter_worker._build_filter_bundle(test_config, None)

    assert bundle.word_filter._sentence_annotation is False
    assert "sentence_annotation=profile.sentence_annotator is not None" in inspect.getsource(
        service_factory.create_services
    )
