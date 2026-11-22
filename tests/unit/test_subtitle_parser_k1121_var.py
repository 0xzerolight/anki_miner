"""K1121 lane VAR: the shadow line for stretched and colloquial hiragana spellings."""

from __future__ import annotations

import re

import pytest

from anki_miner.config.config import AnkiMinerConfig
from anki_miner.models.reading import ReadingUnit
from anki_miner.services import morphology
from anki_miner.services.deinflection import (
    find_highlight_end_with_trace,
    shadow_line,
    shadow_offset,
    shadow_span,
)
from anki_miner.services.morphology import VariantToken, read_variants, word_surface
from anki_miner.services.subtitle_parser import SubtitleParserService


def _fugashi_available() -> bool:
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401

        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")


def _tagged(text: str) -> list:
    tokens = list(SubtitleParserService(AnkiMinerConfig())._tagger()(text))
    for token in tokens:
        token.feature  # noqa: B018 (parsed now: the next tagger call reuses the memory)
    return tokens


def _stretched_wish() -> tuple[str, list]:
    """食べたーい as the pass leaves it: 食べ, then たい placed over たーい."""
    verb, wish = _tagged("食べたい")
    return "食べたーい", [verb, VariantToken(wish, "たーい")]


class TestShadowLine:
    def test_a_variant_token_spells_the_line_and_names_the_standard_word(self):
        text, tokens = _stretched_wish()
        assert "".join(token.surface for token in tokens) == text
        assert [word_surface(token) for token in tokens] == ["食べ", "たい"]
        assert tokens[1].feature.orth == "たーい"
        assert tokens[1].feature.pos1 == "助動詞"

    def test_the_shadow_line_reads_the_standard_spelling(self):
        text, tokens = _stretched_wish()
        shadowed = shadow_line(text, tokens)
        assert shadowed is not None
        shadow_text, shadow_tokens, bounds = shadowed
        assert shadow_text == "食べたい"
        assert [token.surface for token in shadow_tokens] == ["食べ", "たい"]
        assert bounds == [(0, 2, 0, 2), (2, 5, 2, 4)]

    def test_a_line_without_a_variant_has_no_shadow(self):
        assert shadow_line("食べたい", _tagged("食べたい")) is None

    def test_offsets_map_onto_the_shadow_and_back(self):
        text, tokens = _stretched_wish()
        shadowed = shadow_line(text, tokens)
        assert shadowed is not None
        bounds = shadowed[2]
        assert shadow_offset(bounds, 5, to_shadow=True) == 4
        assert shadow_offset(bounds, 4, to_shadow=False) == 5
        # An end inside the respelled token keeps its distance from the token's start.
        assert shadow_offset(bounds, 3, to_shadow=False) == 3

    def test_text_between_and_after_the_tokens_keeps_its_place(self):
        verb, wish, particle = _tagged("食べたいな")
        text = "食べたーい な"
        shadowed = shadow_line(text, [verb, VariantToken(wish, "たーい"), particle])
        assert shadowed is not None
        assert shadowed[0] == "食べたい な"
        assert shadow_offset(shadowed[2], 7, to_shadow=True) == 6
        assert shadow_offset(shadowed[2], 6, to_shadow=False) == 7

    def test_the_highlight_end_is_traced_on_the_shadow_and_mapped_back(self):
        text, tokens = _stretched_wish()
        end, chain = find_highlight_end_with_trace(text, tokens, 0, 2, tokens[0])
        assert end == len(text)
        assert chain

    def test_the_chain_end_is_mapped_back_too(self):
        text, tokens = _stretched_wish()
        end, _chain = find_highlight_end_with_trace(text, tokens, 0, 2, tokens[0], chain_end=True)
        assert end == len(text)

    def test_a_words_own_stretch_is_read_as_its_standard_spelling(self):
        adjective, particle = _tagged("すごいね")
        stretched = VariantToken(adjective, "すごーい")
        assert shadow_span(stretched, "すごーいね", 0, 5) == "すごいね"
        assert shadow_span(particle, "すごーいね", 4, 5) == "ね"
        end, _chain = find_highlight_end_with_trace("すごーいね", [stretched, particle], 0, 4, stretched)
        assert end == 4

    def test_with_no_class_on_a_stretched_line_is_read_as_before(self, monkeypatch):
        monkeypatch.setattr(morphology, "_VARIANT_CLASSES", frozenset())
        tokens = _tagged("ぜーんぶ食べた")
        service = SubtitleParserService(AnkiMinerConfig())
        assert read_variants("ぜーんぶ食べた", tokens, service._tagger(), lambda spelling: 1) is tokens


# What the dictionary of these tests knows. A word with tags is a real entry, a
# row without tags a search-only form that points at the standard word, and a
# kana string may be listed only as the reading of a word written otherwise.
_ENTRIES = {
    "ずーっと": "adv",
    "おーい": "int",
    "すごい": "adj-i",
    "ちがう": "v5u",
    "ない": "adj-i",
    "ゆっくり": "adv",
    "ばか": "n adj-na",
    "みんな": "n adv",
    "どう": "adv",
    "そう": "adv",
    "くる": "vk",
    "どうも": "int adv",
    "こう": "adv",
    "どうして": "adv",
}
_SEARCH_ONLY = frozenset({"すごーい", "ちがーう", "なーい"})
_READINGS = frozenset({"ぜんぶ", "せんせい", "うそ", "やろう"})
_LISTED = frozenset(_ENTRIES) | _SEARCH_ONLY | _READINGS


def _service(*, entries: bool = True) -> SubtitleParserService:
    return SubtitleParserService(
        AnkiMinerConfig(),
        term_lookup=lambda asked: {term for term in asked if term in _LISTED},
        kana_attest_lookup=lambda forms: {form: form in _LISTED for form in forms},
        form_lookup=(
            (lambda asked: {term: [("", _ENTRIES.get(term, ""))] for term in asked if term in _LISTED - _READINGS})
            if entries
            else None
        ),
    )


def _words(text: str, service: SubtitleParserService | None = None) -> list:
    words, _index, _counts = (service or _service()).parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
    )
    return words


def _cards(text: str, service: SubtitleParserService | None = None) -> dict[str, str]:
    """Each card front of the line with the text its highlight covers."""
    return {word.mined_form: text[word.surface_start : word.bold_end] for word in _words(text, service)}


class TestStretchedSpellingTheTaggerResolves:
    """V-K: the tagger knows the word, only its spelling is the stretched one: the card is the standard word."""

    @pytest.mark.parametrize(
        ("text", "front", "written"),
        [
            ("ぜーんぶ食べた", "ぜんぶ", "ぜーんぶ"),
            ("みーんな来たよ", "みんな", "みーんな"),
            ("すごーい", "すごい", "すごーい"),  # listed, but only as a search-only form
            ("それはちがーう", "ちがう", "ちがーう"),
            ("ゆーっくり歩こう", "ゆっくり", "ゆーっくり"),
            ("お金がなーい", "ない", "なーい"),
            ("せんせー、おはよう", "せんせい", "せんせー"),
        ],
    )
    def test_cards_the_standard_word_over_the_text_as_written(self, text, front, written):
        cards = _cards(text)
        assert cards.get(front) == written
        assert written not in cards

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("ずーっと待ってた", "ずーっと"),  # a real headword as written
            ("おーい、こっちだ", "おーい"),
        ],
    )
    def test_a_real_headword_as_written_stays(self, text, front):
        assert front in _cards(text)

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("どーこですか", "どう"),  # a one-kana stem before a stray kana
            ("そーゆうことは言うな", "そう"),  # a one-kana stem before a name the tagger invents
            ("このやろー", "やろう"),  # やろう is another word (やる)
            ("できたてどー", "どう"),  # a stem that ends the clause right after a word
            ("こーくんが来た", "こう"),  # a nickname
            ("こーのー、待て", "こう"),  # この, stretched
            ("カードを切る", "かど"),  # katakana before the mark is no site
            ("もっと食べる", "もと"),
        ],
    )
    def test_a_stretch_that_is_no_known_word_is_left_alone(self, text, front):
        assert front not in _cards(text)

    def test_a_join_over_a_stretched_stem_is_the_entry_as_the_dictionary_spells_it(self):
        assert _cards("どーも") == {"どうも": "どーも"}
        assert _cards("どーして来たの").get("どうして") == "どーして"

    def test_a_small_vowel_that_ends_a_word_is_that_spelling(self):
        _text, raw_tokens, *_rest = _service()._build_line_state("いやぁ、そうか", 0.0, 1.0)
        assert not any(isinstance(token, VariantToken) for token in raw_tokens)

    def test_the_raw_tokens_still_spell_the_line(self):
        service = _service()
        text, raw_tokens, *_rest = service._build_line_state("ぜーんぶ食べた", 0.0, 1.0)
        assert "".join(token.surface for token in raw_tokens) == text
        assert [word_surface(token) for token in raw_tokens][0] == "ぜんぶ"

    def test_the_sentence_and_its_furigana_show_and_bold_the_text_as_written(self):
        text = "それはちがーうよ"
        word = next(word for word in _words(text) if word.mined_form == "ちがう")
        assert word.surface == "ちがーう"
        assert word.sentence == text
        plain = re.search(r"<b>(.*?)</b>", word.sentence_bolded).group(1)
        ruby = re.search(r"<b>(.*?)</b>", word.sentence_furigana_bolded).group(1)
        assert plain == "ちがーう"
        assert re.sub(r"\[[^\]]*\]| ", "", ruby) == "ちがーう"
        assert re.sub(r"\[[^\]]*\]|</?b>| ", "", word.sentence_furigana_bolded) == text

    def test_without_a_dictionary_the_line_is_read_as_before(self):
        service = SubtitleParserService(AnkiMinerConfig())
        _text, raw_tokens, *_rest = service._build_line_state("ぜーんぶ食べた", 0.0, 1.0)
        assert not any(isinstance(token, VariantToken) for token in raw_tokens)

    def test_without_entry_tags_a_listed_spelling_is_a_headword_as_written(self):
        # Search-only rows cannot be told from real entries: nothing listed is re-read.
        assert "すごーい" in _cards("すごーい", _service(entries=False))
