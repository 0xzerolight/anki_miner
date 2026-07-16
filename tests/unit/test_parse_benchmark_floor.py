"""CI floor test for benchmark strategy (b) — the JMdict-anchored resolver.

Strategy (a) (``a-lite-orthbase``) drives the real ``SubtitleParserService``
dict-free, so the resolver safe-degrades and every じる/ずる verb keeps its
archaic 感ずる orthBase — jiru-zuru recall is 0.000. Strategy (b)
(``b-lite-anchor``) drives the SAME real pipeline with a small deterministic
fixture dictionary wired into the parser's offline ``term_lookup``, activating
``resolve_dictionary_form`` so 感じた → 感じる.

This test parses ~30 short corpus sentences through real fugashi/unidic (no
network, no full UniDic, no user ``~/.anki_miner`` — the fixture index is built
under a temp dir at import). It pins two things:

1. The load-bearing assertion: (b) jiru-zuru recall is STRICTLY GREATER than
   (a)'s. Equality would mean the resolver is dead.
2. Absolute floors: (b) clears the jiru-zuru recall floor AND does not regress
   the guard categories that were already correct under (a).

The kana-written floor lands in Task 4 (kana recovery); nominal-suffix and
long-compound are provisional/dict-dependent (Tasks 5/6) — none is gated here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

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


pytestmark = pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")

from scripts.parse_benchmark import (  # noqa: E402
    DEFAULT_CORPUS_DIR,
    junk_rate,
    load_corpus,
    mine_lite_anchor,
    mine_lite_orthbase,
    recall,
    run_benchmark,
)

# Guard categories whose forms are ALREADY correct on main (dict-free). Wiring
# the fixture dict must not regress them: recall stays perfect, no junk appears.
_GUARD_CATEGORIES = (
    "archaic-lemma",
    "cross-conjugation",
    "kanji-variant",
    "potential-ranuki",
    "katakana",
)


def _scored() -> dict:
    records = load_corpus(DEFAULT_CORPUS_DIR)
    return run_benchmark(
        records,
        {"a-lite-orthbase": mine_lite_orthbase, "b-lite-anchor": mine_lite_anchor},
    )


def test_anchor_strictly_beats_orthbase_on_jiru_zuru() -> None:
    results = _scored()
    a_jz = recall(results["a-lite-orthbase"].by_category["jiru-zuru"])
    b_jz = recall(results["b-lite-anchor"].by_category["jiru-zuru"])
    # Load-bearing: the resolver must actually move the needle. Equality means
    # the JMdict-anchored fix never fired.
    assert b_jz > a_jz, f"strategy (b) jiru-zuru recall {b_jz} did not beat (a) {a_jz}"


def test_anchor_meets_jiru_zuru_recall_floor() -> None:
    results = _scored()
    b_jz = recall(results["b-lite-anchor"].by_category["jiru-zuru"])
    # 7 jiru-zuru records; allow one straggler.
    assert b_jz >= 6 / 7, f"strategy (b) jiru-zuru recall {b_jz} below floor 6/7"


def test_anchor_does_not_regress_guard_categories() -> None:
    results = _scored()
    b = results["b-lite-anchor"]
    for category in _GUARD_CATEGORIES:
        counts = b.by_category[category]
        assert recall(counts) == 1.0, f"strategy (b) regressed recall on {category}: {recall(counts)}"
        assert junk_rate(counts) == 0.0, f"strategy (b) introduced junk on {category}: {junk_rate(counts)}"


# NOTE: the kana-written recall floor lands in Task 4 (kana recovery); the
# nominal-suffix / long-compound compound categories are provisional (Tasks
# 5/6). None is gated here.
