"""K1121 lane VAR: the shadow line for stretched and colloquial hiragana spellings."""

from __future__ import annotations

import re
from types import SimpleNamespace

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
from anki_miner.services.morphology import VariantToken, _spoken_nai_stands, read_variants, word_surface
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
    "しかない": "exp adj-i",
    "みっともない": "adj-i",
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


def _located(*tokens: tuple[str, str]) -> list:
    """Hand-made tokens (surface, pos1) glued one after the other, as ``iter_token_spans`` gives them."""
    located, cursor = [], 0
    for surface, pos1 in tokens:
        token = SimpleNamespace(surface=surface, feature=SimpleNamespace(pos1=pos1, lemma=surface))
        located.append((token, cursor, cursor + len(surface)))
        cursor += len(surface)
    return located


class TestSpokenNaiAlone:
    """V-NAI: ねえ the tagger itself reads as 無い, where it stands in a clause, is the word ない, carded ねえ."""

    @pytest.mark.parametrize(
        ("text", "written"),
        [
            ("ここにもねえのかよ", "ねえ"),
            ("才能ねえし", "ねえ"),
            ("そんなもんねぇよ", "ねぇ"),
            ("時間がねーんだ", "ねー"),
            ("用はねえよ", "ねえ"),
        ],
    )
    def test_cards_the_word_as_said(self, text, written):
        cards = _cards(text)
        assert cards.get("ねえ") == written
        assert "ない" not in cards

    @pytest.mark.parametrize(
        "text",
        [
            "ねえ、ちょっと聞いて",  # the call
            "ねえねえ、聞いてよ",
            "そうだねえ",  # the particle
            "それはねえ、困ったんだよ",
            "やるしかねえ",  # しかない is the expression
            "信じらんねえ",  # a piece of the chain
            "見てねえよ",
            "よくねえよ",
            "みっともねえな",  # one word
        ],
    )
    def test_the_call_the_particle_and_a_chain_piece_are_no_card(self, text):
        cards = _cards(text)
        assert "ねえ" not in cards
        assert "ない" not in cards

    @pytest.mark.parametrize(
        ("tokens", "at", "stands"),
        [
            ((("才能", "名詞"), ("ねえ", "形容詞"), ("し", "助詞")), 1, True),
            ((("ケンカ", "名詞"), ("ねえ", "形容詞")), 1, False),  # before a pause: as often the particle
            ((("わけ", "名詞"), ("ねえ", "形容詞")), 1, True),
            ((("あんた", "代名詞"), ("達", "接尾辞"), ("ねぇ", "形容詞")), 2, False),
            ((("痛く", "形容詞"), ("なん", "代名詞"), ("か", "助詞"), ("ねえ", "形容詞"), ("ぞ", "助詞")), 3, False),
            ((("金", "名詞"), ("が", "助詞"), ("ねえ", "形容詞")), 2, True),
            ((("ねえ", "形容詞"), ("の", "助詞")), 0, True),
            ((("ねえ", "形容詞"), ("、", "補助記号")), 0, False),  # the call
            ((("ねえ", "形容詞"), ("ねえ", "形容詞")), 1, False),
            ((("そう", "副詞"), ("ねえ", "形容詞")), 1, False),  # そうねえ
            ((("それ", "代名詞"), ("は", "助詞"), ("ねえ", "形容詞"), ("、", "補助記号")), 2, False),
            ((("ここ", "代名詞"), ("に", "助詞"), ("は", "助詞"), ("ねえ", "形容詞"), ("な", "助詞")), 3, True),
            ((("信じ", "動詞"), ("らん", "助動詞"), ("ねえ", "形容詞")), 2, False),
            ((("見", "動詞"), ("て", "助詞"), ("ねえ", "形容詞")), 2, False),
            ((("みっとも", "名詞"), ("ねえ", "形容詞")), 1, False),  # みっともない is an entry
            ((("キー", "名詞"), ("ねー", "形容詞"), ("ねー", "形容詞")), 1, False),  # a doubled call
            ((("ごめん", "名詞"), ("ねー", "形容詞"), ("とか", "助詞")), 1, False),
            ((("甘く", "形容詞"), ("なんか", "助詞"), ("ねえ", "形容詞"), ("よ", "助詞")), 2, False),
            ((("見", "動詞"), ("たく", "助動詞"), ("も", "助詞"), ("ねえ", "形容詞"), ("や", "助詞")), 3, False),
            ((("見", "動詞"), ("づらく", "接尾辞"), ("ねー", "形容詞"), ("か", "助詞")), 2, False),
            ((("意味", "名詞"), ("なんか", "助詞"), ("ねえ", "形容詞"), ("よ", "助詞")), 2, True),
            ((("全く", "副詞"), ("ねえ", "形容詞"), ("や", "助詞")), 1, True),
        ],
    )
    def test_where_the_word_stands(self, tokens, at, stands):
        assert _spoken_nai_stands(_located(*tokens), at, lambda spelling: 2 if spelling in _ENTRIES else 0) is stands

    def test_a_long_kana_run_listed_as_a_reading_is_that_entry(self):
        def listed_as_a_reading(spelling: str) -> int:
            return 1 if spelling in ("おとなげない", "もんない") else 0

        grown_up = _located(("おとな", "名詞"), ("げ", "接尾辞"), ("ねえ", "形容詞"), ("よ", "助詞"))
        assert _spoken_nai_stands(grown_up, 2, listed_as_a_reading) is False
        thing = _located(("もん", "名詞"), ("ねえ", "形容詞"), ("よ", "助詞"))
        assert _spoken_nai_stands(thing, 1, listed_as_a_reading) is True

    def test_the_sentence_shows_and_bolds_the_word_as_written(self):
        text = "そんなもんねぇよ"
        word = next(word for word in _words(text) if word.mined_form == "ねえ")
        assert word.surface == "ねぇ"
        assert re.search(r"<b>(.*?)</b>", word.sentence_bolded).group(1) == "ねぇ"
        assert re.sub(r"\[[^\]]*\]| ", "", re.search(r"<b>(.*?)</b>", word.sentence_furigana_bolded).group(1)) == "ねぇ"
