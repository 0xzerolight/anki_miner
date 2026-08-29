"""Korean mined-form rule, Sejong POS defaults, and the real inclusion gate."""

import json
from pathlib import Path

import pytest

from anki_miner.languages.ko import morphology as ko_morph
from anki_miner.languages.token import LanguageToken
from anki_miner.models.word import select_mined_form
from anki_miner.services.morphology import TokenInclusionRule

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "ko" / "tokens.jsonl"


def _rows():
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]


def _rule(excluded=None) -> TokenInclusionRule:
    return TokenInclusionRule(
        allowed_pos=frozenset(ko_morph.KO_ALLOWED_POS),
        excluded_subtypes=frozenset(ko_morph.KO_EXCLUDED_SUBTYPES if excluded is None else excluded),
    )


@pytest.mark.parametrize(
    ("pos", "lemma", "surface", "expected"),
    [
        ("VV", "먹다", "먹", "먹다"),  # kiwi lemma already carries 다
        ("VV", "먹", "먹", "먹다"),  # bare stem -> append 다
        ("VA-I", "춥다", "추", "춥다"),  # raw suffixed tag tolerated
        ("VX", "있다", "있", "있다"),
        ("NN", "학생", "학생", "학생"),  # nouns mine as surface
        ("SH", "漢字", "漢字", "漢字"),
        (None, "", "학생", "학생"),  # pos is Optional on the protocol
    ],
)
def test_mined_form_appends_da_for_predicates_only(pos, lemma, surface, expected):
    assert ko_morph.KoreanMinedForm().mined_form(pos, "", lemma, surface, None) == expected


def test_mined_form_matches_the_protocol_default_for_pronunciation():
    assert ko_morph.KoreanMinedForm().mined_form("NN", "", "학생", "학생") == "학생"


def test_the_ja_selection_rule_passes_a_korean_front_through_untouched():
    # models/word.py:207 stays the single card-front rule; ko pos values never
    # hit its 動詞/形容詞/名詞/代名詞 branches, so the parser's answer survives.
    assert select_mined_form("VV", "먹", "먹", "먹다", None) == "먹다"
    assert select_mined_form("NN", "학생", "학생", "학생", None) == "학생"


def test_fixture_tokens_round_trip_through_the_mined_form_rule():
    for row in _rows():
        got = ko_morph.KoreanMinedForm().mined_form(row["pos1"], "", row["lemma"], row["surface"], None)
        assert got == row["mined_form"], row


def test_pos_defaults_allow_content_classes_only():
    allowed = set(ko_morph.KO_ALLOWED_POS)
    assert {"VV", "VA", "NN", "MA", "XR"} <= allowed
    assert not any(tag.startswith(("JK", "JX", "JC", "E", "SF", "Z_")) for tag in allowed)


def test_excluded_subtypes_drop_words_the_class_gate_admits():
    bound_noun = LanguageToken("수", "NN", "NNB", "수", "")
    conjunction = LanguageToken("그러나", "MA", "MAJ", "그러나", "")
    common_noun = LanguageToken("학생", "NN", "NNG", "학생", "")
    strict, permissive = _rule(), _rule(excluded=())
    assert strict.content_gate_ok(common_noun) is True
    assert strict.content_gate_ok(bound_noun) is False
    assert strict.content_gate_ok(conjunction) is False
    # Proves it is the SUBTYPE gate firing, not allowed_pos: with the exclusions
    # emptied the very same tokens pass.
    assert permissive.content_gate_ok(bound_noun) is True
    assert permissive.content_gate_ok(conjunction) is True


def test_the_real_gate_keeps_content_words_and_drops_grammar_tokens():
    rule = _rule()
    kept, dropped = [], []
    for row in _rows():
        token = LanguageToken(row["surface"], row["pos1"], row["pos2"], row["lemma"], "")
        (kept if rule.content_gate_ok(token) else dropped).append(row["pos2"] or row["pos1"])
    assert {"NNG", "VV"} <= set(kept)
    assert {"JKO", "EF", "SF", "NNB"} <= set(dropped)


def test_every_allowed_class_and_excluded_subtype_has_a_ui_label():
    labels = set(ko_morph.KO_POS_LABELS)
    assert set(ko_morph.KO_ALLOWED_POS) <= labels
    assert set(ko_morph.KO_EXCLUDED_SUBTYPES) <= labels


def test_latin_runs_are_not_advertised_as_mineable():
    """The script gate rejects a pure-Latin run before POS is ever consulted.

    Listing SL as an allowed class promised mining the gate cannot deliver, and
    put a class in the settings POS editor that does nothing when ticked.
    """
    from anki_miner.languages.ko.script import KoreanScript

    script = KoreanScript()
    gated = TokenInclusionRule(
        allowed_pos=frozenset(ko_morph.KO_ALLOWED_POS),
        excluded_subtypes=frozenset(ko_morph.KO_EXCLUDED_SUBTYPES),
        script_gate=script.contains_target_script,
    )
    latin = LanguageToken("Netflix", "SL", "", "Netflix", "")
    assert script.contains_target_script("Netflix") is False
    assert gated.should_include(latin) is False

    assert "SL" not in ko_morph.KO_ALLOWED_POS
    assert "SL" not in ko_morph.KO_POS_LABELS
    # The classes that DO survive the same gate stay allowed.
    assert {"NN", "SH", "VV"} <= set(ko_morph.KO_ALLOWED_POS)
    assert gated.should_include(LanguageToken("학생", "NN", "NNG", "학생", "")) is True
    assert gated.should_include(LanguageToken("銀行", "SH", "", "銀行", "")) is True
