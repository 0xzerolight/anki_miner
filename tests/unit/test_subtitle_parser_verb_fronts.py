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
