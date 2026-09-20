"""The three parser seams the zh factory closes (ZH-010, ZH-011, S1).

Each one is a Japanese assumption that reaches Chinese only because the factory
passed nothing: the dictionary compound matcher (inert for zh -- its synthetics
carry UniDic POS names no zh token can match), the ellipsis truncation guard
(a single hanzi is a whole word, not a severed fragment), and the normaliser.

The yue twin lives here too: Cantonese is as single-character-dense as
Mandarin, so ``languages/yue/parser.py`` closes the ellipsis guard for the same
reason and the two must not drift apart.
"""

from __future__ import annotations

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.subtitle_parser import SubtitleParserService

#: A drama stutter line: two ellipsis groups, and the word it is about (钱) is
#: one character long and sits right against the first of them.
ZH_STUTTER = "钱…钱不见了…全都没了。"
YUE_STUTTER = "錢…錢唔見咗…全部冇晒。"

#: 大家好。 with 大 written as the Kangxi radical U+2F24, the substitution OCR
#: and legacy sources make. Escaped rather than pasted because the radical
#: renders identically to the ideograph -- in an editor as much as on a card.
ZH_RADICAL_LINE = "\u2f24家好。"

#: Everything the Japanese caption strip deletes and Chinese keeps: a
#: continuation arrow (➡), a device marker (📱), a private-use codepoint,
#: U+FFFD, and a squared unit the compatibility fold would rewrite to "m2".
ZH_DECORATED_LINE = "➡ 这个房子有120㎡📱\ue000\ufffd。"


def _attest_nothing(surfaces: list[str]) -> set[str]:
    return set()


def _config(code: str) -> AnkiMinerConfig:
    return switch_language(AnkiMinerConfig(), code)


def _parser(code: str, **kwargs):
    return get_profile(code).create_parser(_config(code), **kwargs)


def _factory_kwargs(monkeypatch, code: str) -> dict[str, object]:
    """What ``code``'s factory hands the service, captured in place of building it."""
    from anki_miner.services import subtitle_parser as module

    seen: dict[str, object] = {}
    monkeypatch.setattr(module, "SubtitleParserService", lambda config, **kwargs: seen.update(kwargs))
    get_profile(code).create_parser(_config(code))
    return seen


def _words(parser, line: str):
    words, _index, _counts = parser.parse_text_units(
        [ReadingUnit(text=line, index=0, location_label="t")], want_line_index=False
    )
    return words


def _mine(parser, line: str) -> set[str]:
    return {word.mined_form for word in _words(parser, line)}


def _sentences(parser, line: str) -> set[str]:
    return {word.sentence for word in _words(parser, line)}


class TestTheEllipsisGuardSeam:
    def test_a_zh_stutter_line_keeps_its_single_character_word(self) -> None:
        assert _mine(_parser("zh"), ZH_STUTTER) == {"钱", "不见", "全都", "没"}

    def test_a_yue_stutter_line_keeps_its_single_character_word(self) -> None:
        assert _mine(_parser("yue"), YUE_STUTTER) == {"錢", "唔見", "全部", "冇"}

    def test_both_factories_close_the_seam(self, monkeypatch) -> None:
        """Asserted on the KWARGS, the way the yue factory's own seams are."""
        assert _factory_kwargs(monkeypatch, "zh")["ellipsis_fragment_guard"] is False
        assert _factory_kwargs(monkeypatch, "yue")["ellipsis_fragment_guard"] is False

    def test_an_explicit_argument_still_wins(self) -> None:
        assert _parser("zh", ellipsis_fragment_guard=True)._ellipsis_fragment_guard is True


class TestTheCompoundMatcherSeam:
    def test_the_zh_factory_builds_no_matcher_with_a_dictionary_wired(self) -> None:
        parser = _parser("zh", term_lookup=_attest_nothing)
        assert parser._compound_matcher is None
        # Only the matcher is closed: the merge gate keeps the same probe.
        assert parser._attest is not None

    def test_the_ja_parser_still_builds_one(self, test_config) -> None:
        assert SubtitleParserService(test_config, term_lookup=_attest_nothing)._compound_matcher is not None


class TestTheNormaliseSeam:
    def test_the_zh_parser_takes_the_profile_normaliser(self) -> None:
        assert _parser("zh").normalize is get_profile("zh").normalize

    def test_the_ja_parser_keeps_the_japanese_pair(self, test_config) -> None:
        assert SubtitleParserService(test_config).normalize is None

    def test_a_radical_substituted_line_still_mines_its_word(self) -> None:
        assert _mine(_parser("zh"), ZH_RADICAL_LINE) == {"大家", "好"}

    def test_the_stored_sentence_shows_the_unified_ideograph(self) -> None:
        assert _sentences(_parser("zh"), ZH_RADICAL_LINE) == {"大家好。"}

    def test_caption_decoration_and_squared_units_survive(self) -> None:
        """What the subtitle wrote is what the card shows; only radicals fold."""
        assert _sentences(_parser("zh"), ZH_DECORATED_LINE) == {ZH_DECORATED_LINE}
