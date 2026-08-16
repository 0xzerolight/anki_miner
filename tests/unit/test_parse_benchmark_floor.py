"""CI floor test for benchmark strategy (b) — the JMdict-anchored resolver.

Strategy (a) (``a-lite-orthbase``) drives the real ``SubtitleParserService``
dict-free, so the resolver safe-degrades and every じる/ずる verb keeps its
archaic 感ずる orthBase — jiru-zuru recall is 0.000. Strategy (b)
(``b-lite-anchor``) drives the SAME real pipeline with a small deterministic
fixture dictionary wired into the parser's offline ``term_lookup``, activating
``resolve_dictionary_form`` so 感じた → 感じる.

This test parses ~30 short corpus sentences through real fugashi/unidic (no
network, no full UniDic, no user ``~/.anki_miner`` — the fixture index is built
under a temp dir at import). It pins three things:

1. The load-bearing assertions: (b) jiru-zuru recall, (b) kana-written recall
   AND (b) nominal-suffix f1 are each STRICTLY GREATER than (a)'s. Equality
   would mean the resolver / kana recovery / attested-or-bail merge gate is
   dead (dict-free strategy (a) fires none of them).
2. Absolute floors: (b) clears the jiru-zuru recall floor, clears the
   kana-written recall floor, is perfect (recall 1.0 / junk 0.0) on the
   finalized nominal-suffix corpus, AND does not regress the guard categories
   that were already correct under (a).
3. Exact post-resolver fronts: (b) exercises the commonness-gated katakana fold
   and same-kanji remap, while (a) retains the observed dict-free fronts.

Aux-context pins the 非自立可能 kana-recovery reject: its fixtures deliberately
attest いる/ある/くれる/おく/しまう so the floor can only be green because the
pos2 reject fires, never via a fixture-dict miss (the false-safe class this
suite exists to prevent). Linebreak-split is scoreboard-only (G4 incidence
measurement, no fix shipped).
"""

from __future__ import annotations

import json
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

from anki_miner.models.reading import ReadingUnit  # noqa: E402
from anki_miner.orchestration.audio_stage import _expression_audio_candidates  # noqa: E402
from scripts.parse_benchmark import (  # noqa: E402
    DEFAULT_CORPUS_DIR,
    _get_service,
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
    # All 6 kana-written records must resolve, including archaic かんずる → かんじる.
    assert b_kw == 1.0, f"strategy (b) kana-written recall {b_kw} below 1.0"


def test_anchor_does_not_regress_guard_categories() -> None:
    results = _scored()
    b = results["b-lite-anchor"]
    for category in _GUARD_CATEGORIES:
        counts = b.by_category[category]
        assert recall(counts) == 1.0, f"strategy (b) regressed recall on {category}: {recall(counts)}"
        assert junk_rate(counts) == 0.0, f"strategy (b) introduced junk on {category}: {junk_rate(counts)}"


def test_anchor_meets_katakana_fragment_floor() -> None:
    results = _scored()
    b_kf = results["b-lite-anchor"].by_category["katakana-fragment"]
    assert junk_rate(b_kf) == 0.0, f"strategy (b) katakana-fragment junk_rate {junk_rate(b_kf)} above 0.0"
    assert mine_lite_anchor("アイスベア") == set()
    # No dictionary ⇒ no compound matcher ⇒ guard inactive: preserve both
    # components as the byte-identical safe-degrade baseline.
    assert mine_lite_orthbase("アイスベア") == {"アイス", "ベア"}


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
    # attested compound stays whole (刑務所/不可能/不可能性/重要性), every ordinary
    # unattested one bails to its bare noun (状況/会議/反応), and an unattested
    # kinship tail stops at the licensed 兄ちゃん boundary — no misses, no junk.
    assert recall(b_ns) == 1.0, f"strategy (b) nominal-suffix recall {recall(b_ns)} below 1.0"
    assert junk_rate(b_ns) == 0.0, f"strategy (b) nominal-suffix junk_rate {junk_rate(b_ns)} above 0.0"
    assert mine_lite_orthbase("食べる方") == {"食べる"}
    assert mine_lite_anchor("食べる方") == {"食べる"}
    assert mine_lite_orthbase("兄ちゃん的には") == {"兄ちゃん的"}
    assert mine_lite_anchor("兄ちゃん的には") == {"兄ちゃん"}


def test_both_strategies_meet_verb_nominalizer_floor() -> None:
    results = _scored()
    for strategy in ("a-lite-orthbase", "b-lite-anchor"):
        counts = results[strategy].by_category["verb-nominalizer"]
        assert recall(counts) == 1.0, f"{strategy} verb-nominalizer recall {recall(counts)} below 1.0"
        assert junk_rate(counts) == 0.0, f"{strategy} verb-nominalizer junk_rate {junk_rate(counts)} above 0.0"
    assert mine_lite_orthbase("読み方を学ぶ") == {"読み方", "学ぶ"}
    assert mine_lite_anchor("読み方を学ぶ") == {"読み方", "学ぶ"}


def test_anchor_strictly_beats_orthbase_on_colloquial() -> None:
    results = _scored()
    a_co = recall(results["a-lite-orthbase"].by_category["colloquial"])
    b_co = recall(results["b-lite-anchor"].by_category["colloquial"])
    # Load-bearing: すげえ/すげー/やべえ/うめえ/わかんない are kana orthBases only
    # the attested kana recovery can mine; dict-free (a) gets 食う alone.
    assert b_co > a_co, f"strategy (b) colloquial recall {b_co} did not beat (a) {a_co}"


def test_anchor_meets_colloquial_floor() -> None:
    results = _scored()
    b_co = results["b-lite-anchor"].by_category["colloquial"]
    # Tripwire, not a fix: unidic-lite's orthBase is ALREADY modern for these
    # (すげえ/すげー→すごい). Perfect score pins that; junk would mean a wrong form
    # (e.g. the kanji lemma 凄い) or a reject regression (する from しちゃった).
    assert recall(b_co) == 1.0, f"strategy (b) colloquial recall {recall(b_co)} below 1.0"
    assert junk_rate(b_co) == 0.0, f"strategy (b) colloquial junk_rate {junk_rate(b_co)} above 0.0"


def test_anchor_meets_lexicalized_window_floor() -> None:
    results = _scored()
    b_lw = results["b-lite-anchor"].by_category["lexicalized-window"]
    assert junk_rate(b_lw) == 0.0, f"strategy (b) lexicalized-window junk_rate {junk_rate(b_lw)} above 0.0"
    # The standalone recovery proves すむ is attested; the joined attestation is
    # therefore the only reason strategy (b) suppresses it inside すみません.
    assert mine_lite_anchor("すみます") == {"すむ"}
    assert mine_lite_anchor("すみません") == set()
    # No dictionary keeps the byte-identical safe-degrade baseline.
    assert mine_lite_orthbase("すみません") == set()


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
    # Number+counter chains die on the inherited 数詞 subtype exclusion whether
    # the merge gate fires or not. A whitespace-stitched chain must not consume
    # a later contiguous lexical token with the same surface.
    for strategy in ("a-lite-orthbase", "b-lite-anchor"):
        counts = results[strategy].by_category["counter"]
        assert recall(counts) == 1.0, f"{strategy} counter recall {recall(counts)} below 1.0"
        assert junk_rate(counts) == 0.0, f"{strategy} counter junk_rate {junk_rate(counts)} above 0.0"


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
    # tripwire instead. The reject fixtures cover two DISTINCT removal kinds:
    # 合…/タ… イガ…/欲し… are fragment junk (truncation debris that is not a real
    # word), whereas 声… is the intended recall-sacrifice of a legit single-char
    # noun caught in a stutter line — accepted collateral loss on the junk ledger,
    # not junk in itself. Every fixture mines its fragment PRE-guard (proven by the
    # pre-guard probe and the wired TestEllipsisTruncationGuard unit tests), so a
    # revert makes junk_rate>0. recall==1.0 pins that no keep-case (夢…/夢……/
    # 待って…/行こう…) was over-rejected. Like the guard it mirrors, this category
    # floor is intentionally lossy — a change-detector against the reject's own
    # fixtures, not independent ground truth.
    assert junk_rate(b_el) == 0.0, f"strategy (b) ellipsis-truncation junk_rate {junk_rate(b_el)} above 0.0"
    assert recall(b_el) == 1.0, f"strategy (b) ellipsis-truncation recall {recall(b_el)} below 1.0"
    assert mine_lite_orthbase("アプリケーションプログラム… アプリケーションプログラム…") == {"アプリケーション"}


def test_anchor_meets_classical_adjective_floor() -> None:
    results = _scored()
    b_ca = results["b-lite-anchor"].by_category["classical-adjective"]
    # V4: the classical 連体形 ク-stem fold (美しき→美しい) lives in mining_base and is
    # DICT-FREE, so it fires the same on (a) and (b) — a regression tripwire, not a
    # strict-beat. A revert mines the bare ク-stem 美し (junk) and misses 美しい, so
    # both recall<1 and junk>0; 良き still folds via the ('し','い') swap pair and
    # いい stays unfolded.
    assert recall(b_ca) == 1.0, f"strategy (b) classical-adjective recall {recall(b_ca)} below 1.0"
    assert junk_rate(b_ca) == 0.0, f"strategy (b) classical-adjective junk_rate {junk_rate(b_ca)} above 0.0"


def test_anchor_meets_vowel_elongation_floor() -> None:
    results = _scored()
    # V5: the vowel-elongation 名詞 fold (手ぇ→手) lives in select_mined_form and is
    # DICT-FREE (UniDic pronunciation evidence), so it fires the same on (a) and
    # (b) — a regression tripwire. A revert misses 手/気; an over-broad fold mints
    # 舞 as junk and misses lexical 舞い. Loanwords stay on the surface.
    for strategy in ("a-lite-orthbase", "b-lite-anchor"):
        counts = results[strategy].by_category["vowel-elongation"]
        assert recall(counts) == 1.0, f"{strategy} vowel-elongation recall {recall(counts)} below 1.0"
        assert junk_rate(counts) == 0.0, f"{strategy} vowel-elongation junk_rate {junk_rate(counts)} above 0.0"


def test_anchor_meets_katakana_pronoun_floor() -> None:
    results = _scored()
    b_kp = results["b-lite-anchor"].by_category["katakana-pronoun"]
    # V6: the katakana-pronoun fold (ワタシ→私, オマエ→お前) lives in select_mined_form
    # via a curated 5-entry map and is DICT-FREE (string-only), so it fires the same
    # on (a) and (b) — a regression tripwire, not a strict-beat. A revert mines the
    # katakana surface ワタシ/オマエ (junk) and misses the kanji 私/お前, so both
    # recall<1 and junk>0; アナタ/ワイ are absent from the map and stay on the surface
    # (membership-only), so a fold that over-reaches to them also trips junk here.
    assert recall(b_kp) == 1.0, f"strategy (b) katakana-pronoun recall {recall(b_kp)} below 1.0"
    assert junk_rate(b_kp) == 0.0, f"strategy (b) katakana-pronoun junk_rate {junk_rate(b_kp)} above 0.0"


def test_both_strategies_meet_reading_override_floor() -> None:
    results = _scored()
    for strategy in ("a-lite-orthbase", "b-lite-anchor"):
        counts = results[strategy].by_category["reading-override"]
        assert recall(counts) == 1.0, f"{strategy} reading-override recall {recall(counts)} below 1.0"
        assert junk_rate(counts) == 0.0, f"{strategy} reading-override junk_rate {junk_rate(counts)} above 0.0"


def test_both_strategies_meet_reading_overrides_front_floor() -> None:
    results = _scored()
    for strategy in ("a-lite-orthbase", "b-lite-anchor"):
        counts = results[strategy].by_category["reading-overrides"]
        assert recall(counts) == 1.0, f"{strategy} reading-overrides recall {recall(counts)} below 1.0"
        assert junk_rate(counts) == 0.0, f"{strategy} reading-overrides junk_rate {junk_rate(counts)} above 0.0"


def test_reading_override_details_match_mined_fronts() -> None:
    record = next(rec for rec in load_corpus(DEFAULT_CORPUS_DIR) if rec["id"] == "ro01")
    expected_readings = record["expected_readings"]
    words, _index, _counts = _get_service().parse_text_units(
        [ReadingUnit(text=record["sentence"], index=0, location_label="benchmark")],
        want_line_index=False,
    )
    readings_by_front = {word.mined_form: word.expression_reading for word in words}

    assert set(expected_readings) == set(record["expected"])
    assert {front: readings_by_front[front] for front in expected_readings} == expected_readings


def test_anchor_meets_katakana_verb_front_floor() -> None:
    assert mine_lite_orthbase("ヤラれた") == {"ヤル"}
    assert mine_lite_anchor("ヤラれた") == {"やる"}


def test_anchor_meets_front_remap_floor() -> None:
    assert mine_lite_orthbase("神を恐る") == {"神", "恐る"}
    assert mine_lite_anchor("神を恐る") == {"神", "恐れる"}


def test_orthbase_meets_kana_runs_floor() -> None:
    results = _scored()
    # V8 pins strategy (a), NOT (b): the merged-token junk (獅子+子 → シシシ, 3-run シ)
    # only survives UNGATED dict-free, so strategy (a) is where content_gate_ok's
    # ≥3-identical-kana reject actually removes it. Under (b) the attest-or-bail
    # merge gate decomposes シシシ on its own (獅子子 unattested), which would mask a
    # revert. A revert re-mints シシシ/メメメ under (a) ⇒ junk_rate>0. recall==1.0 pins
    # that the reject does not over-reach: バナナ (2-run ナナ), スーパー (excluded ー)
    # and ヒヒ (2-run 狒々) still mine. The kana-recovery-death case (どおおおおっ→
    # 覆う) needs an attesting dict absent from the locked anchor, so its kill is
    # proven in the wired TestRepeatedKanaRunReject unit tests, not here.
    a_kr = results["a-lite-orthbase"].by_category["kana-runs"]
    assert junk_rate(a_kr) == 0.0, f"strategy (a) kana-runs junk_rate {junk_rate(a_kr)} above 0.0"
    assert recall(a_kr) == 1.0, f"strategy (a) kana-runs recall {recall(a_kr)} below 1.0"


def test_pos_suffix_lemma_strip_folds_potential() -> None:
    # V10: 引けいって → 引け carries the decorated lemma 引く-他動詞. Stripping the fine
    # POS tail (extract_lemma's endswith broadening) unblocks the ('ける','く')
    # potential fold, so the card front is the base 引く. The line also lives in
    # potential_ranuki.jsonl (the guard-category floor covers it); this pins the
    # exact form dict-free AND with the anchor. A revert mines 引ける (fold blocked
    # by the decorated lemma). 引け occurs in neither replay corpus, so this is the
    # sole corpus witness.
    assert mine_lite_orthbase("引けいって") == {"引く"}
    assert mine_lite_anchor("引けいって") == {"引く"}
    # S11a-007: pr05 covers the six remaining godan e-row fold pairs. Pin the
    # dictionary-independent fold under both benchmark strategies.
    godan_potentials = {"買う", "泳ぐ", "話す", "死ぬ", "遊ぶ", "読む"}
    assert mine_lite_orthbase("買える 泳げる 話せる 死ねる 遊べる 読める") == godan_potentials
    assert mine_lite_anchor("買える 泳げる 話せる 死ねる 遊べる 読める") == godan_potentials


def test_form_identity_assertion_corpus() -> None:
    path = DEFAULT_CORPUS_DIR / "form_identity" / "unsafe_lemma.jsonl"
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record == {
        "id": "fi01",
        "sentence": "帰れる。",
        "expected_fronts": ["帰れる"],
        "assertions": [
            "definition fallback must not resolve 返る",
            "expression-audio candidates must not contain 返る/かえる",
        ],
    }

    words, _index, _counts = _get_service().parse_text_units(
        [ReadingUnit(record["sentence"], 0, "form-identity")],
        want_line_index=False,
    )
    word = next(word for word in words if word.mined_form == "帰れる")

    assert [word.mined_form] == record["expected_fronts"]
    assert ("返る", "かえる") not in _expression_audio_candidates(word)


def test_anchor_strictly_beats_orthbase_on_masu_stem_nominal() -> None:
    results = _scored()
    a_ms = f1(results["a-lite-orthbase"].by_category["masu-stem-nominal"])
    b_ms = f1(results["b-lite-anchor"].by_category["masu-stem-nominal"])
    # The nominalizer is attestation-gated, so dict-free (a) cannot fire and
    # keeps unidic's verb front. Equality would mean the pass never ran.
    assert b_ms > a_ms, f"strategy (b) masu-stem-nominal f1 {b_ms} did not beat (a) {a_ms}"


def test_anchor_meets_masu_stem_nominal_floor() -> None:
    results = _scored()
    b_ms = results["b-lite-anchor"].by_category["masu-stem-nominal"]
    assert recall(b_ms) == 1.0, f"strategy (b) masu-stem-nominal recall {recall(b_ms)} below 1.0"
    assert junk_rate(b_ms) == 0.0, f"(b) masu-stem-nominal junk_rate {junk_rate(b_ms)} above 0.0"
    # The bug itself: dict-free keeps unidic's 差し入れる, the anchor mines the noun.
    assert mine_lite_orthbase("これ、差し入れ みんなで食べてよ") == {"差し入れる", "食べる"}
    assert mine_lite_anchor("これ、差し入れ みんなで食べてよ") == {"差し入れ", "食べる"}
    # Neighbour allow-list: 帰り/笑い/動き are all attested, so these can only
    # stay verbs while the gate holds.
    assert mine_lite_anchor("帰り ましょう") == {"帰る"}
    assert mine_lite_anchor("笑い ながら歩く") == {"歩く", "笑う"}
    assert mine_lite_anchor("動き 出した") == {"出す", "動く"}
    # A final 連用形 token has no neighbour and must never fire.
    assert mine_lite_anchor("早く帰り") == {"帰る", "早い"}


def test_anchor_strictly_beats_orthbase_on_prefix_compound() -> None:
    results = _scored()
    a_pc = f1(results["a-lite-orthbase"].by_category["prefix-compound"])
    b_pc = f1(results["b-lite-anchor"].by_category["prefix-compound"])
    # Dict-free drops the 接頭辞 and ships the resolver's 存ずる/存じる.
    assert b_pc > a_pc, f"strategy (b) prefix-compound f1 {b_pc} did not beat (a) {a_pc}"


def test_anchor_meets_prefix_compound_floor() -> None:
    results = _scored()
    b_pc = results["b-lite-anchor"].by_category["prefix-compound"]
    assert recall(b_pc) == 1.0, f"strategy (b) prefix-compound recall {recall(b_pc)} below 1.0"
    assert junk_rate(b_pc) == 0.0, f"(b) prefix-compound junk_rate {junk_rate(b_pc)} above 0.0"
    assert mine_lite_orthbase("ご存じですか") == {"存ずる"}
    assert mine_lite_anchor("ご存じですか") == {"ご存じ"}
    # Narrow-gate proof: 気をつけ IS attested in the fixture index, so a
    # 名詞-headed span taking the surface join would mine it and fail here.
    assert mine_lite_anchor("気をつけて帰る") == {"気をつける", "帰る"}


# NOTE: jiru-zuru (Task 3), kana-written (Task 4), nominal-suffix (Task 5),
# colloquial/counter (A2), aux-context (A1), long-compound (Task 6/Q2),
# ellipsis-truncation (U8), katakana-verb-front, front-remap, masu-stem-nominal
# and prefix-compound floors are gated above; linebreak-split is
# scoreboard-only.
