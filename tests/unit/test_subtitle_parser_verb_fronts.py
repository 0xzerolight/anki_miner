"""Verb card fronts: imperatives, potentials, passives and causatives.

The fixture records live in ``tests/fixtures/parse_corpus/verb_voice.jsonl`` and
run through the real parser wired to the benchmark's fixture dictionary.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.services.subtitle_parser import SubtitleParserService

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _fugashi_available() -> bool:
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401

        return True
    except ImportError:
        return False


def _verb_voice_records() -> list[dict]:
    from scripts.parse_benchmark import DEFAULT_CORPUS_DIR, load_corpus

    return [r for r in load_corpus(DEFAULT_CORPUS_DIR) if r["category"] == "verb-voice"]


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
@pytest.mark.parametrize("record", _verb_voice_records(), ids=lambda r: r["id"])
def test_verb_voice_fixture(record: dict) -> None:
    from scripts.parse_benchmark import mine_lite_anchor

    assert mine_lite_anchor(record["sentence"]) == set(record["expected"])


def _verb_token(surface: str, orth_base: str, c_type: str, c_form: str, lemma: str | None = None) -> MagicMock:
    token = MagicMock()
    token.surface = surface
    token.feature.pos1 = "動詞"
    token.feature.pos2 = "一般"
    token.feature.lemma = lemma or orth_base
    token.feature.orthBase = orth_base
    token.feature.cType = c_type
    token.feature.cForm = c_form
    token.feature.lForm = None
    token.feature.kanaBase = None
    return token


def _service(test_config, attested: set[str]) -> SubtitleParserService:
    def rules_lookup(candidates: list[tuple[str, int]]) -> set[str]:
        return {text for text, _mask in candidates if text in attested}

    with patch("anki_miner.services.subtitle_parser.get_shared_tagger"):
        return SubtitleParserService(
            test_config,
            term_lookup=lambda terms: {t for t in terms if t in attested},
            term_rules_lookup=rules_lookup,
        )


class TestGodanImperativeFront:
    """A token the tagger reads as a godan imperative keeps its own verb as the front."""

    def test_imperative_is_not_carded_as_the_ichidan_verb(self, test_config):
        service = _service(test_config, {"持てる", "持つ"})
        token = _verb_token("持て", "持つ", "五段-タ行", "命令形")
        assert service._resolve_front(token, "持つ", "持て", 0, 2) == "持つ"

    def test_ichidan_stem_still_resolves(self, test_config):
        # The じる/ずる rewrite the resolver exists for is not a godan imperative.
        service = _service(test_config, {"感じる"})
        token = _verb_token("感じ", "感ずる", "サ行変格", "連用形-一般")
        assert service._resolve_front(token, "感ずる", "感じた", 0, 3) == "感じる"


class TestPotentialFoldInSourceSpelling:
    """A potential whose tagger lemma is spelled otherwise folds to the base verb in the line's spelling."""

    @staticmethod
    def _potential(orth_base: str, lemma: str, l_form: str, kana_base: str) -> MagicMock:
        token = _verb_token(orth_base, orth_base, "下一段-ラ行", "終止形-一般", lemma=lemma)
        token.feature.lForm = l_form
        token.feature.kanaBase = kana_base
        return token

    def test_kanji_swapped_lemma_folds_on_the_source_stem(self, test_config):
        service = _service(test_config, {"帰る", "返る"})
        token = self._potential("帰れる", "返る", "カエル", "カエレル")
        assert service._resolve_front(token, "帰れる", "帰れる", 0, 3) == "帰る"

    def test_attested_potential_spelling_is_kept(self, test_config):
        service = _service(test_config, {"近づける", "近づく", "近付く"})
        token = self._potential("近づける", "近付く", "チカヅク", "チカヅケル")
        assert service._resolve_front(token, "近づける", "近づける", 0, 4) == "近づける"

    def test_unattested_fold_keeps_the_front(self, test_config):
        service = _service(test_config, {"返る"})
        token = self._potential("帰れる", "返る", "カエル", "カエレル")
        assert service._resolve_front(token, "帰れる", "帰れる", 0, 3) == "帰れる"

    def test_reading_that_is_not_the_swap_does_not_fold(self, test_config):
        # 帰れる read as something other than the potential of its lemma.
        service = _service(test_config, {"帰る", "返す"})
        token = self._potential("帰れる", "返す", "カエス", "カエレル")
        assert service._resolve_front(token, "帰れる", "帰れる", 0, 3) == "帰れる"

    def test_kana_spelling_is_left_alone(self, test_config):
        # いれる read as the ra-nuki potential of いる: the exact-headword probe
        # attests いる but cannot see いれる (淹れる), so a kana front never folds.
        service = _service(test_config, {"いる"})
        token = self._potential("いれる", "居る", "イル", "イレル")
        assert service._resolve_front(token, "いれる", "いれる", 0, 3) == "いれる"


class TestShortCausativeFront:
    """A short causative UniDic lemmatizes to an unattested -す verb cards the base verb."""

    @staticmethod
    def _causative(orth_base: str, reading: str) -> MagicMock:
        token = _verb_token(orth_base, orth_base, "五段-サ行", "未然形-一般")
        token.feature.lForm = reading
        token.feature.kanaBase = reading
        return token

    def test_godan_short_causative_cards_the_base_verb(self, test_config):
        service = _service(test_config, {"行く"})
        token = self._causative("行かす", "イカス")
        assert service._resolve_front(token, "行かす", "行かさ", 0, 3) == "行く"

    def test_ichidan_short_causative_cards_the_base_verb(self, test_config):
        service = _service(test_config, {"食べる"})
        token = self._causative("食べさす", "タベサス")
        assert service._resolve_front(token, "食べさす", "食べさす", 0, 4) == "食べる"

    def test_attested_asu_verb_is_kept(self, test_config):
        service = _service(test_config, {"聞かす", "聞く"})
        token = self._causative("聞かす", "キカス")
        assert service._resolve_front(token, "聞かす", "聞かす", 0, 3) == "聞かす"

    def test_kanji_before_sasu_is_the_godan_verb(self, test_config):
        # 出さす is the short causative of 出す or of 出る; only a kana e/i-row
        # stem proves the ichidan reading, so 出さす cards 出す.
        service = _service(test_config, {"出す", "出る"})
        token = self._causative("出さす", "ダサス")
        assert service._resolve_front(token, "出さす", "出さす", 0, 3) == "出す"


class TestGodanPassiveFront:
    """A godan 未然形 the tagger splits from a passive れる keeps its own verb as the front."""

    def test_passive_is_not_carded_as_the_aru_verb(self, test_config):
        service = _service(test_config, {"刺す", "刺さる"})
        token = _verb_token("刺さ", "刺す", "五段-サ行", "未然形-一般")
        assert service._resolve_front(token, "刺す", "刺された", 0, 4) == "刺す"

    def test_kana_stem_is_left_to_the_resolver(self, test_config):
        # なされた is mostly the honorific なさる; only a kanji stem takes the rule.
        service = _service(test_config, {"なす", "なさる"})
        token = _verb_token("なさ", "なす", "五段-サ行", "未然形-一般")
        assert service._resolve_front(token, "なす", "なされた", 0, 4) == "なさる"

    def test_honorific_kudasaru_is_kept(self, test_config):
        # 来て下された is the honorific 下さる, not the passive of 下す.
        service = _service(test_config, {"下す", "下さる"})
        token = _verb_token("下さ", "下す", "五段-サ行", "未然形-一般")
        assert service._resolve_front(token, "下す", "下された", 0, 4) == "下さる"

    def test_lexicalized_passive_headword_is_kept(self, test_config):
        # 救われる is a headword of its own; only the -aru verb leaves the pool.
        service = _service(test_config, {"救う", "救われる"})
        token = _verb_token("救わ", "救う", "五段-ワア行", "未然形-一般")
        assert service._resolve_front(token, "救う", "救われた", 0, 4) == "救われる"


class TestKatakanaVerbFoldToLemmaReading:
    """An all-katakana verb orthBase that is no verb entry folds to its lemma's reading."""

    @staticmethod
    def _service(test_config, *, verbs=(), others=(), readings=None):
        from anki_miner.services.deinflection import condition_flags_from_rules, conditions_match

        rules = {**dict.fromkeys(others, ""), **dict.fromkeys(verbs, "v5")}
        readings = readings or {}
        with patch("anki_miner.services.subtitle_parser.get_shared_tagger"):
            return SubtitleParserService(
                test_config,
                term_lookup=lambda terms: {t for t in terms if t in rules},
                term_common_lookup=lambda terms: dict.fromkeys(terms, False),
                term_rules_lookup=lambda candidates: {
                    text
                    for text, mask in candidates
                    if text in rules
                    and (conditions_match(mask, condition_flags_from_rules(rules[text])) if mask else not rules[text])
                },
                reading_lookup=lambda terms: {t: readings[t] for t in terms if t in readings},
            )

    def test_non_verb_katakana_entry_folds_to_the_lemma_reading(self, test_config):
        service = self._service(test_config, verbs=("振る",), others=("フル",), readings={"振る": ["ふる"]})
        assert service._fold_katakana_verb_front("フル", "振る") == "ふる"

    def test_katakana_verb_entry_is_kept(self, test_config):
        service = self._service(test_config, verbs=("イク", "行く"), readings={"行く": ["いく", "ゆく"]})
        assert service._fold_katakana_verb_front("イク", "行く") == "イク"

    def test_lemma_read_otherwise_keeps_the_front(self, test_config):
        service = self._service(test_config, verbs=("振る",), others=("フル",), readings={"振る": ["ふる"]})
        assert service._fold_katakana_verb_front("ツク", "振る") == "ツク"

    def test_katakana_adjective_keeps_the_old_gate(self, test_config):
        # No lemma is passed for an adjective: カワイイ stays katakana by ruling.
        service = self._service(test_config, others=("カワイイ",), readings={"可愛い": ["かわいい"]})
        assert service._fold_katakana_verb_front("カワイイ") == "カワイイ"
