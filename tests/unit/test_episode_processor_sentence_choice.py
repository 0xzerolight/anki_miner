"""Sentence choice in EpisodeProcessor: the line index (F-06, F-07), phase-2 order, profile dispatch."""

from __future__ import annotations

import collections
import logging
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.languages.registry import bound_mined_form, get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models import LineLemmas, NotMinedReason, TokenizedWord
from anki_miner.models.reading import ReadingDocument, ReadingUnit
from anki_miner.orchestration.episode_processor import EpisodeProcessor, _EpisodeContext
from anki_miner.services.word_filter import WordFilterService
from tests.conftest import build_processor
from tests.unit._processor_fixtures import make_word

_LOGGER = "anki_miner.orchestration.episode_processor"

#: Every phase-2 knob, pinned so only the one a test turns on acts.
_KNOBS = {
    "include_known_words": False,
    "use_known_words_db": False,
    "use_whitelist": False,
    "min_frequency_rank": 0,
    "max_frequency_rank": 0,
    "exclude_hiragana_only_words": False,
    "exclude_katakana_only_words": False,
    "deduplicate_sentences": False,
    "use_i_plus_one_filter": False,
    "pick_best_sentence": False,
    "max_sentence_duration_seconds": 0.0,
    "max_sentence_chars": 0,
    "bold_target_in_sentence": False,
    "bypass_optional_filters": False,
}


def _word(front: str, sentence: str, start: float, *, language: str = "ja") -> TokenizedWord:
    """A noun first seen on ``sentence``; outside ja its card front is pinned to ``front``."""
    return TokenizedWord(
        surface=front,
        lemma=front,
        reading="",
        sentence=sentence,
        start_time=start,
        end_time=start + 2.0,
        duration=2.0,
        pos="名詞" if language == "ja" else "NOUN",
        mined_form_override="" if language == "ja" else front,
    )


def _line(text: str, start: float, *fronts: str, duration: float = 2.0) -> LineLemmas:
    """A parsed line carrying ``fronts`` (each front is its own surface on the line)."""
    spans = tuple((front, front, text.index(front), text.index(front) + len(front), -1) for front in fronts)
    return LineLemmas(
        text,
        frozenset(fronts),
        start,
        start + duration,
        duration,
        front_spans=spans,
        fronts=frozenset(fronts),
    )


def _config(test_config, language: str = "ja", **overrides):
    base = test_config if language == "ja" else switch_language(test_config, language)
    return replace(base, **{**_KNOBS, **overrides})


def _filter(config, language: str = "ja") -> WordFilterService:
    """The word filter service_factory would build for ``language`` (no tagger)."""
    if language == "ja":
        return WordFilterService(config)
    profile = get_profile(language)
    return WordFilterService(
        config,
        mined_form=bound_mined_form(profile, config),
        script=profile.script,
        dedup_fold=profile.dedup_fold,
        expression_tracks_surface=getattr(profile.mined_form, "expression_tracks_surface", None),
        sentence_annotation=profile.sentence_annotator is not None,
    )


def _processor(config, language: str = "ja") -> EpisodeProcessor:
    """A real processor and word filter; every word has a dictionary entry and none is known."""
    definitions = MagicMock(name="definition_service")
    definitions.has_offline_definitions.side_effect = lambda terms: dict.fromkeys(terms, True)
    definitions.offline_deinflection_terms_exist.return_value = set()
    definitions.offline_term_identities.return_value = {}
    anki = MagicMock(name="anki_service")
    anki.get_existing_vocabulary.return_value = set()
    return build_processor(
        config,
        presenter=MagicMock(name="presenter"),
        word_filter=_filter(config, language),
        definition_service=definitions,
        anki_service=anki,
    )


def _phase2(processor: EpisodeProcessor, words, line_index):
    ctx = _EpisodeContext(0.0, "", "", "episode", "series", "")
    kept = processor._phase2_filter(ctx, words, line_index, collapse=False)
    return kept, ctx


def _document() -> ReadingDocument:
    return ReadingDocument(
        title="Book",
        kind="book",
        series="Book",
        episode="ch1",
        units=[ReadingUnit(text="unit0", index=0, location_label="p.0")],
    )


# --- The line index: built whenever phase 2 reads it ------------------------


def test_one_card_per_sentence_builds_the_line_index_without_a_curator(test_config, mock_services, tmp_path):
    config = replace(test_config, deduplicate_sentences=True)
    word = make_word("食べる")
    parser = mock_services["subtitle_parser"]
    parser.parse_subtitle_file_with_index.side_effect = lambda f, offset=None: ([word], [])
    processor = build_processor(config, **mock_services)

    with patch.object(EpisodeProcessor, "_phase2_filter", return_value=[]):
        processor.process_episode(tmp_path / "ep.mkv", tmp_path / "ep.ass")

    parser.parse_subtitle_file_with_index.assert_called_once_with(tmp_path / "ep.ass", None)
    parser.parse_subtitle_file.assert_not_called()


def test_no_sentence_picker_keeps_the_plain_parse(test_config, mock_services, tmp_path):
    word = make_word("食べる")
    parser = mock_services["subtitle_parser"]
    parser.parse_subtitle_file.return_value = [word]
    processor = build_processor(test_config, **mock_services)

    with patch.object(EpisodeProcessor, "_phase2_filter", return_value=[]):
        processor.process_episode(tmp_path / "ep.mkv", tmp_path / "ep.ass")

    parser.parse_subtitle_file.assert_called_once_with(tmp_path / "ep.ass", None)
    parser.parse_subtitle_file_with_index.assert_not_called()


def test_reading_builds_the_line_index_for_one_card_per_sentence(test_config, mock_services):
    config = replace(test_config, deduplicate_sentences=True)
    parser = mock_services["subtitle_parser"]
    parser.parse_text_units.return_value = ([], None, collections.Counter())
    processor = build_processor(config, **mock_services)

    processor.process_reading(_document())

    assert parser.parse_text_units.call_args.args[1] is True


def test_reading_without_a_sentence_picker_skips_the_line_index(test_config, mock_services):
    parser = mock_services["subtitle_parser"]
    parser.parse_text_units.return_value = ([], None, collections.Counter())
    processor = build_processor(test_config, **mock_services)

    processor.process_reading(_document())

    assert parser.parse_text_units.call_args.args[1] is False


# --- F-06 in phase 2 ---------------------------------------------------------


def test_phase_two_moves_a_losing_word_and_reports_the_rest(test_config, caplog):
    config = _config(test_config, deduplicate_sentences=True)
    line_a = _line("猫と犬と鳥がいる。", 0.0, "猫", "犬", "鳥")
    line_b = _line("犬が走る。", 10.0, "犬")
    words = [_word(front, line_a.line_text, 0.0) for front in ("猫", "犬", "鳥")]
    processor = _processor(config)

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        kept, ctx = _phase2(processor, words, [line_a, line_b])

    assert [(word.mined_form, word.sentence) for word in kept] == [("猫", line_a.line_text), ("犬", "犬が走る。")]
    assert ctx.not_mined == {"鳥": NotMinedReason.ONE_PER_SENTENCE}
    assert "Sentence dedup: moved=1 words=2" in caplog.text


def test_es_phase_two_moves_a_losing_word(test_config):
    """Review Focus 3: the es profile's front policy and fold through F-06."""
    config = _config(test_config, "es", deduplicate_sentences=True)
    line_a = _line("el perro y el gato", 0.0, "perro", "gato")
    line_b = _line("un gato negro", 5.0, "gato")
    words = [_word(front, line_a.line_text, 0.0, language="es") for front in ("perro", "gato")]
    processor = _processor(config, "es")

    kept, ctx = _phase2(processor, words, [line_a, line_b])

    assert [(word.mined_form, word.sentence) for word in kept] == [
        ("perro", "el perro y el gato"),
        ("gato", "un gato negro"),
    ]
    assert ctx.not_mined == {}


def test_without_a_line_index_phase_two_keeps_first_wins(test_config):
    config = _config(test_config, deduplicate_sentences=True)
    line_a = _line("猫と犬がいる。", 0.0, "猫", "犬")
    words = [_word(front, line_a.line_text, 0.0) for front in ("猫", "犬")]
    processor = _processor(config)

    kept, ctx = _phase2(processor, words, None)

    assert [word.mined_form for word in kept] == ["猫"]
    assert ctx.not_mined == {"犬": NotMinedReason.ONE_PER_SENTENCE}


# --- F-07 in phase 2 ---------------------------------------------------------


def test_best_sentence_builds_the_line_index_without_a_curator(test_config, mock_services, tmp_path):
    config = replace(test_config, pick_best_sentence=True)
    word = make_word("食べる")
    parser = mock_services["subtitle_parser"]
    parser.parse_subtitle_file_with_index.side_effect = lambda f, offset=None: ([word], [])
    processor = build_processor(config, **mock_services)

    with patch.object(EpisodeProcessor, "_phase2_filter", return_value=[]):
        processor.process_episode(tmp_path / "ep.mkv", tmp_path / "ep.ass")

    parser.parse_subtitle_file_with_index.assert_called_once_with(tmp_path / "ep.ass", None)
    parser.parse_subtitle_file.assert_not_called()


def test_reading_builds_the_line_index_for_best_sentence(test_config, mock_services):
    config = replace(test_config, pick_best_sentence=True)
    parser = mock_services["subtitle_parser"]
    parser.parse_text_units.return_value = ([], None, collections.Counter())
    processor = build_processor(config, **mock_services)

    processor.process_reading(_document())

    assert parser.parse_text_units.call_args.args[1] is True


def test_phase_two_picks_the_clearest_line(test_config, caplog):
    config = _config(test_config, pick_best_sentence=True)
    crowded = _line("犬と鳥と魚がいる。", 0.0, "犬", "鳥", "魚")
    clear = _line("犬がいる。", 10.0, "犬")
    words = [_word(front, crowded.line_text, 0.0) for front in ("犬", "鳥", "魚")]
    processor = _processor(config)

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        kept, ctx = _phase2(processor, words, [crowded, clear])

    assert [(word.mined_form, word.sentence) for word in kept] == [
        ("犬", "犬がいる。"),
        ("鳥", crowded.line_text),
        ("魚", crowded.line_text),
    ]
    assert kept[0].start_time == pytest.approx(10.0)
    assert kept[1] is words[1]
    assert ctx.not_mined == {}
    assert "Best sentence: moved=1 words=3" in caplog.text


def test_best_sentence_off_keeps_the_first_line(test_config):
    """A config saved before F-07 loads pick_best_sentence False (F0): phase 2 is unchanged."""
    config = _config(test_config)
    crowded = _line("犬と鳥と魚がいる。", 0.0, "犬", "鳥", "魚")
    clear = _line("犬がいる。", 10.0, "犬")
    words = [_word(front, crowded.line_text, 0.0) for front in ("犬", "鳥", "魚")]

    kept, _ctx = _phase2(_processor(config), words, [crowded, clear])

    assert kept == words
    assert all(after is before for after, before in zip(kept, words, strict=True))


def test_a_bypass_run_keeps_the_first_line(test_config):
    """Deck Builder and the golden contract skip every coverage step, F-07 included."""
    config = _config(test_config, pick_best_sentence=True, bypass_optional_filters=True)
    crowded = _line("犬と鳥と魚がいる。", 0.0, "犬", "鳥", "魚")
    clear = _line("犬がいる。", 10.0, "犬")
    words = [_word(front, crowded.line_text, 0.0) for front in ("犬", "鳥", "魚")]

    kept, _ctx = _phase2(_processor(config), words, [crowded, clear])

    assert kept[0] is words[0]


def test_best_sentence_runs_before_one_card_per_sentence(test_config):
    """F-07 first: 猫 and 犬 leave the crowded line for clearer ones, so dedup drops nobody.

    Dedup first would keep 猫 on the crowded line, move 犬 to the pair line and drop 鳥.
    """
    config = _config(test_config, pick_best_sentence=True, deduplicate_sentences=True)
    crowded = _line("猫と犬と鳥がいる。", 0.0, "猫", "犬", "鳥")
    pair = _line("猫と犬が遊ぶ。", 10.0, "猫", "犬")
    single = _line("犬が走る。", 20.0, "犬")
    words = [_word(front, crowded.line_text, 0.0) for front in ("猫", "犬", "鳥")]

    kept, ctx = _phase2(_processor(config), words, [crowded, pair, single])

    assert [(word.mined_form, word.sentence) for word in kept] == [
        ("猫", pair.line_text),
        ("犬", single.line_text),
        ("鳥", crowded.line_text),
    ]
    assert ctx.not_mined == {}


def test_after_i_plus_one_the_clearest_i_plus_one_line_wins(test_config):
    """i+1 alone takes the earliest i+1 line; F-07 then prefers the one inside the length band."""
    config = _config(test_config, pick_best_sentence=True, use_i_plus_one_filter=True)
    terse = _line("犬だ。", 0.0, "犬")  # i+1 for 犬, 3 characters: outside the band
    full = _line("公園で大きな犬が走っている。", 10.0, "犬")  # i+1 for 犬, 14 characters
    crowded = _line("犬と鳥がいる公園の朝です。", 20.0, "犬", "鳥")  # two unknowns: i+1 for nobody
    dog = _word("犬", terse.line_text, 0.0)
    bird = _word("鳥", crowded.line_text, 20.0)

    kept, _ctx = _phase2(_processor(config), [dog, bird], [terse, full, crowded])
    plain, _ctx = _phase2(_processor(replace(config, pick_best_sentence=False)), [dog, bird], [terse, full, crowded])

    assert [(word.mined_form, word.sentence) for word in kept] == [("犬", full.line_text)]
    assert [(word.mined_form, word.sentence) for word in plain] == [("犬", terse.line_text)]


def test_with_i_plus_one_the_best_sentence_never_leaves_the_i_plus_one_lines(test_config):
    """A word whose only i+1 line breaks the caps is dropped as before, never moved to an i+2 line."""
    config = _config(test_config, pick_best_sentence=True, use_i_plus_one_filter=True, max_sentence_chars=12)
    long = _line("公園で大きな犬が元気に走っている。", 0.0, "犬")  # i+1 for 犬, over the 12-character cap
    crowded = _line("犬と鳥がいる。", 10.0, "犬", "鳥")  # inside the cap, two unknowns: i+1 for nobody
    dog = _word("犬", long.line_text, 0.0)
    bird = _word("鳥", crowded.line_text, 10.0)

    kept, _ctx = _phase2(_processor(config), [dog, bird], [long, crowded])

    assert kept == []


@pytest.mark.parametrize(
    ("language", "terse", "full", "front"),
    [
        # es spaces words: 3 words is under the 4-word floor, 12 words is inside the band.
        # Counted in characters both would be inside the band and the shorter (terse) would win.
        ("es", "el perro come", "mi perro grande y negro duerme todo el día en su sofá", "perro"),
        # zh does not: 3 characters is under the 10-character floor, 16 is inside.
        # Counted in whitespace words both lines are one word and the earlier (terse) would win.
        ("zh", "我的狗", "我昨天在公园里看见了一只很大的狗", "狗"),
    ],
)
def test_the_profile_decides_how_length_is_measured(test_config, language, terse, full, front):
    """Review Focus 3: SentenceRules.space_aware comes from the run's own profile."""
    config = _config(test_config, language, pick_best_sentence=True)
    lines = [_line(terse, 0.0, front), _line(full, 10.0, front)]
    word = _word(front, terse, 0.0, language=language)

    kept, _ctx = _phase2(_processor(config, language), [word], lines)

    assert [w.sentence for w in kept] == [full]
