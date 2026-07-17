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

1. The load-bearing assertions: (b) jiru-zuru recall, (b) kana-written recall
   AND (b) nominal-suffix f1 are each STRICTLY GREATER than (a)'s. Equality
   would mean the resolver / kana recovery / attested-or-bail merge gate is
   dead (dict-free strategy (a) fires none of them).
2. Absolute floors: (b) clears the jiru-zuru recall floor, clears the
   kana-written recall floor, is perfect (recall 1.0 / junk 0.0) on the
   finalized nominal-suffix corpus, AND does not regress the guard categories
   that were already correct under (a).

Aux-context pins the 非自立可能 kana-recovery reject: its fixtures deliberately
attest いる/ある/くれる/おく/しまう so the floor can only be green because the
pos2 reject fires, never via a fixture-dict miss (the false-safe class this
suite exists to prevent). Linebreak-split is scoreboard-only (G4 incidence
measurement, no fix shipped).
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
    f1,
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


def test_anchor_strictly_beats_orthbase_on_kana_written() -> None:
    results = _scored()
    a_kw = recall(results["a-lite-orthbase"].by_category["kana-written"])
    b_kw = recall(results["b-lite-anchor"].by_category["kana-written"])
    # Load-bearing (WS2): the script gate drops ALL pure-hiragana content words
    # dict-free, so (a) recovers none. (b) must actually recover the category.
    assert b_kw > a_kw, f"strategy (b) kana-written recall {b_kw} did not beat (a) {a_kw}"


def test_anchor_meets_kana_written_recall_floor() -> None:
    results = _scored()
    b_kw = recall(results["b-lite-anchor"].by_category["kana-written"])
    # 5 kana-written records (きれい/すごい/かわいい/あざとい/しがない); allow one straggler.
    assert b_kw >= 4 / 5, f"strategy (b) kana-written recall {b_kw} below floor 4/5"


def test_anchor_does_not_regress_guard_categories() -> None:
    results = _scored()
    b = results["b-lite-anchor"]
    for category in _GUARD_CATEGORIES:
        counts = b.by_category[category]
        assert recall(counts) == 1.0, f"strategy (b) regressed recall on {category}: {recall(counts)}"
        assert junk_rate(counts) == 0.0, f"strategy (b) introduced junk on {category}: {junk_rate(counts)}"


def test_anchor_strictly_beats_orthbase_on_nominal_suffix() -> None:
    results = _scored()
    a_ns = f1(results["a-lite-orthbase"].by_category["nominal-suffix"])
    b_ns = f1(results["b-lite-anchor"].by_category["nominal-suffix"])
    # Load-bearing (Task 5): the attested-or-bail gate is dict-only, so dict-free
    # strategy (a) keeps the junk compounds (状況的/会議中/超反応/重要). Equality
    # would mean the gate never fired.
    assert b_ns > a_ns, f"strategy (b) nominal-suffix f1 {b_ns} did not beat (a) {a_ns}"


def test_anchor_meets_nominal_suffix_floor() -> None:
    results = _scored()
    b_ns = results["b-lite-anchor"].by_category["nominal-suffix"]
    # The gate must be perfect on the finalized nominal-suffix corpus: every
    # attested compound stays whole (刑務所/不可能/重要性) and every unattested one
    # bails to exactly its bare noun (状況/会議/反応) — no misses, no junk.
    assert recall(b_ns) == 1.0, f"strategy (b) nominal-suffix recall {recall(b_ns)} below 1.0"
    assert junk_rate(b_ns) == 0.0, f"strategy (b) nominal-suffix junk_rate {junk_rate(b_ns)} above 0.0"


def test_anchor_strictly_beats_orthbase_on_colloquial() -> None:
    results = _scored()
    a_co = recall(results["a-lite-orthbase"].by_category["colloquial"])
    b_co = recall(results["b-lite-anchor"].by_category["colloquial"])
    # Load-bearing: すげえ/やべえ/うめえ/わかんない are pure-kana orthBases only
    # the attested kana recovery can mine; dict-free (a) gets 食う alone.
    assert b_co > a_co, f"strategy (b) colloquial recall {b_co} did not beat (a) {a_co}"


def test_anchor_meets_colloquial_floor() -> None:
    results = _scored()
    b_co = results["b-lite-anchor"].by_category["colloquial"]
    # Tripwire, not a fix: unidic-lite's orthBase is ALREADY modern for these
    # (すげえ→すごい). Perfect score pins that; junk would mean a wrong form
    # (e.g. the kanji lemma 凄い) or a reject regression (する from しちゃった).
    assert recall(b_co) == 1.0, f"strategy (b) colloquial recall {recall(b_co)} below 1.0"
    assert junk_rate(b_co) == 0.0, f"strategy (b) colloquial junk_rate {junk_rate(b_co)} above 0.0"


def test_anchor_meets_aux_context_floor() -> None:
    results = _scored()
    b_ac = results["b-lite-anchor"].by_category["aux-context"]
    # Load-bearing (A1): the fixtures deliberately ATTEST いる/ある/くれる/おく/
    # しまう, so only the 非自立可能 pos2 reject keeps them out of the mined set.
    # Junk here means the reject was reverted and every ている line mints an aux
    # card again; a miss means a real content word (猫/見る/読む…) was lost.
    assert recall(b_ac) == 1.0, f"strategy (b) aux-context recall {recall(b_ac)} below 1.0"
    assert junk_rate(b_ac) == 0.0, f"strategy (b) aux-context junk_rate {junk_rate(b_ac)} above 0.0"


def test_anchor_meets_aux_keijoushi_floor() -> None:
    results = _scored()
    b_ak = results["b-lite-anchor"].by_category["aux-keijoushi"]
    # Load-bearing: よう/みたい/そう are JMdict-attested pure hiragana, so absent
    # the 助動詞語幹 pos2 reject the kana-recovery pass would mint them as junk
    # content words. This is the real-tagger floor the sibling 非自立可能 reject
    # already had (aux-context) but 助動詞語幹 previously only had a mock test.
    assert recall(b_ak) == 1.0, f"strategy (b) aux-keijoushi recall {recall(b_ak)} below 1.0"
    assert junk_rate(b_ak) == 0.0, f"strategy (b) aux-keijoushi junk_rate {junk_rate(b_ak)} above 0.0"


def test_counter_category_is_clean() -> None:
    results = _scored()
    b_ct = results["b-lite-anchor"].by_category["counter"]
    # Number+counter chains die on the inherited 数詞 subtype exclusion whether
    # the merge gate fires or not; only the real verb survives.
    assert recall(b_ct) == 1.0, f"strategy (b) counter recall {recall(b_ct)} below 1.0"
    assert junk_rate(b_ct) == 0.0, f"strategy (b) counter junk_rate {junk_rate(b_ct)} above 0.0"


def test_anchor_meets_long_compound_floor() -> None:
    results = _scored()
    b_lc = results["b-lite-anchor"].by_category["long-compound"]
    # Task 6 (Q2): attested 2-token compounds merge whole — including the
    # 13-char katakana case only the 16-char span cap admits — while the
    # deliberately-attested 14-char greeting still fragments on the 5-token cap.
    assert recall(b_lc) == 1.0, f"strategy (b) long-compound recall {recall(b_lc)} below 1.0"
    assert junk_rate(b_lc) == 0.0, f"strategy (b) long-compound junk_rate {junk_rate(b_lc)} above 0.0"


def test_anchor_meets_ellipsis_floor() -> None:
    results = _scored()
    b_el = results["b-lite-anchor"].by_category["ellipsis-truncation"]
    # U8: the ellipsis truncation-fragment reject is DICT-FREE, so it fires the
    # same on (a) and (b) — no strict-beat is available. This is a regression
    # tripwire instead: every reject fixture (合…/タ… イガ…/欲し…/声…) mines its
    # fragment as junk PRE-guard (proven by the pre-guard probe and the wired
    # TestEllipsisTruncationGuard unit tests), so a revert makes junk_rate>0.
    # recall==1.0 pins that no keep-case (夢…/夢……/待って…/行こう…) was over-rejected.
    assert junk_rate(b_el) == 0.0, f"strategy (b) ellipsis-truncation junk_rate {junk_rate(b_el)} above 0.0"
    assert recall(b_el) == 1.0, f"strategy (b) ellipsis-truncation recall {recall(b_el)} below 1.0"


# NOTE: jiru-zuru (Task 3), kana-written (Task 4), nominal-suffix (Task 5),
# colloquial/counter (A2), aux-context (A1), long-compound (Task 6/Q2) and
# ellipsis-truncation (U8) floors are gated above; linebreak-split is
# scoreboard-only.
