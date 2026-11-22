"""K1121 lane VAR: the shadow line for stretched and colloquial hiragana spellings."""

from __future__ import annotations

import pytest

from anki_miner.config.config import AnkiMinerConfig
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
