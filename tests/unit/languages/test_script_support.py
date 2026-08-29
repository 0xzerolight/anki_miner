"""ScriptSupport dispatch in the script-type filter.

Task 1A.8. ``filter_by_script_type`` used to hard-code the three kana
predicates; it now walks a set of option ids and asks a ``ScriptSupport`` to
decide each one. Japanese must not move a single word:

* the ja profile declares exactly the three ids the old body implemented,
* the ``enabled_options=None`` path reproduces the old three-branch derivation
  (``mixed_kana_only`` only when BOTH booleans are on), and
* injecting ``get_profile("ja").script`` changes no survivor.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from anki_miner.languages.registry import get_profile
from anki_miner.models import TokenizedWord
from anki_miner.services.word_filter import (
    WordFilterService,
    enabled_script_options,
    script_options_kwarg,
)

# ぜんぶ  — noun, mines as surface: hiragana-only
# コーヒー — noun: katakana-only
# サボる  — verb, mines as orthBase: BOTH kana scripts, neither one alone
# 学校    — noun: kanji, never dropped
_CORPUS_SPECS = (
    ("ぜんぶ", "名詞", ""),
    ("コーヒー", "名詞", ""),
    ("サボる", "動詞", "サボる"),
    ("学校", "名詞", ""),
)

_BOOL_COMBINATIONS = ((False, False), (True, False), (False, True), (True, True))


def _word(lemma: str, pos: str, orth_base: str) -> TokenizedWord:
    return TokenizedWord(
        surface=lemma,
        lemma=lemma,
        reading="",
        sentence="テスト",
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
        pos=pos,
        orth_base=orth_base,
    )


def _corpus() -> list[TokenizedWord]:
    return [_word(*spec) for spec in _CORPUS_SPECS]


# --- (a) the ja profile declares the three ids the old body implemented ------


def test_ja_filter_options_are_the_three_kana_ids():
    options = get_profile("ja").script.filter_options()

    assert tuple(opt.option_id for opt in options) == (
        "hiragana_only",
        "katakana_only",
        "mixed_kana_only",
    )
    assert tuple(opt.config_field for opt in options) == (
        "exclude_hiragana_only_words",
        "exclude_katakana_only_words",
        "",
    )
    # Every option carries a label for the settings section to render.
    assert all(opt.label for opt in options)


# --- (b) injecting the ja adapter moves no word -----------------------------


@pytest.mark.parametrize(("hira", "kata"), _BOOL_COMBINATIONS)
def test_injected_ja_script_yields_identical_survivors(test_config, hira, kata):
    plain = WordFilterService(test_config)
    routed = WordFilterService(test_config, script=get_profile("ja").script)

    expected = plain.filter_by_script_type(_corpus(), exclude_hiragana_only=hira, exclude_katakana_only=kata)
    actual = routed.filter_by_script_type(_corpus(), exclude_hiragana_only=hira, exclude_katakana_only=kata)

    assert [w.mined_form for w in actual] == [w.mined_form for w in expected]


def test_the_four_combinations_have_the_pinned_survivors(test_config):
    """Guards the parametrized identity above against a two-sided regression."""
    service = WordFilterService(test_config, script=get_profile("ja").script)

    def survivors(hira, kata):
        kept = service.filter_by_script_type(_corpus(), exclude_hiragana_only=hira, exclude_katakana_only=kata)
        return [w.mined_form for w in kept]

    assert survivors(False, False) == ["ぜんぶ", "コーヒー", "サボる", "学校"]
    assert survivors(True, False) == ["コーヒー", "サボる", "学校"]
    assert survivors(False, True) == ["ぜんぶ", "サボる", "学校"]
    assert survivors(True, True) == ["学校"]


# --- (c) an explicit option set replaces the boolean derivation -------------


def test_enabled_options_match_the_equivalent_boolean(test_config):
    service = WordFilterService(test_config, script=get_profile("ja").script)

    by_boolean = service.filter_by_script_type(_corpus(), exclude_hiragana_only=True)
    by_options = service.filter_by_script_type(_corpus(), enabled_options=frozenset({"hiragana_only"}))

    assert [w.mined_form for w in by_options] == [w.mined_form for w in by_boolean]
    assert [w.mined_form for w in by_options] == ["コーヒー", "サボる", "学校"]


def test_empty_enabled_options_drops_nothing(test_config):
    """An empty set is not "no set": it must never fall back to the booleans."""
    service = WordFilterService(test_config, script=get_profile("ja").script)

    kept = service.filter_by_script_type(
        _corpus(),
        exclude_hiragana_only=True,
        exclude_katakana_only=True,
        enabled_options=frozenset(),
    )

    assert [w.mined_form for w in kept] == ["ぜんぶ", "コーヒー", "サボる", "学校"]


# --- (d) the adapter's own predicate dispatch -------------------------------


@pytest.mark.parametrize(
    ("option_id", "form", "expected"),
    [
        ("mixed_kana_only", "サボる", True),
        ("mixed_kana_only", "ぜんぶ", False),
        ("hiragana_only", "ぜんぶ", True),
        ("hiragana_only", "すごーい", True),  # ー carries no script of its own
        ("katakana_only", "コーヒー", True),
        ("katakana_only", "ぜんぶ", False),
        ("hiragana_only", "学校", False),
        ("unknown_option", "ぜんぶ", False),
    ],
)
def test_ja_script_matches(option_id, form, expected):
    assert get_profile("ja").script.matches(option_id, form) is expected


def _config_with(config, hira, kata):
    """The config a settings pair of kana checkboxes produces."""
    return replace(
        config,
        exclude_hiragana_only_words=hira,
        exclude_katakana_only_words=kata,
    )


# --- the caller-side derivation both call sites share -----------------------


@pytest.mark.parametrize(
    ("hira", "kata", "expected"),
    [
        (False, False, frozenset()),
        (True, False, frozenset({"hiragana_only"})),
        (False, True, frozenset({"katakana_only"})),
        (True, True, frozenset({"hiragana_only", "katakana_only", "mixed_kana_only"})),
    ],
)
def test_enabled_script_options_reproduces_the_three_branch_derivation(test_config, hira, kata, expected):
    config = _config_with(test_config, hira, kata)

    assert enabled_script_options(get_profile("ja").script, config) == expected


@pytest.mark.parametrize(("hira", "kata"), _BOOL_COMBINATIONS)
def test_derived_options_give_the_boolean_survivors(test_config, hira, kata):
    """What a call site now passes must move exactly the words the flags did."""
    config = _config_with(test_config, hira, kata)
    service = WordFilterService(config, script=get_profile("ja").script)

    by_boolean = service.filter_by_script_type(_corpus(), exclude_hiragana_only=hira, exclude_katakana_only=kata)
    by_derived = service.filter_by_script_type(
        _corpus(),
        exclude_hiragana_only=hira,
        exclude_katakana_only=kata,
        enabled_options=enabled_script_options(get_profile("ja").script, config),
    )

    assert [w.mined_form for w in by_derived] == [w.mined_form for w in by_boolean]


def test_options_with_no_field_backed_switch_stay_off(test_config):
    """A language declaring only implicit options never enables one by accident."""

    class _AllImplicit:
        def filter_options(self):
            option = get_profile("ja").script.filter_options()[2]  # config_field == ""
            return (option,)

        def matches(self, option_id, form):  # pragma: no cover - not reached
            raise AssertionError("filter must be skipped on an empty option set")

        def contains_target_script(self, text):  # pragma: no cover - unused here
            return True

    both_on = _config_with(test_config, True, True)
    assert enabled_script_options(_AllImplicit(), both_on) == frozenset()


# --- the omit-when-ja kwarg seam -------------------------------------------


def test_script_options_kwarg_omits_the_keyword_for_ja():
    """ja must keep the pre-extraction call shape: the None path re-derives it."""
    options = frozenset({"hiragana_only", "katakana_only", "mixed_kana_only"})

    assert script_options_kwarg(options, "ja") == {}


@pytest.mark.parametrize("language", ["ko", "zh"])
def test_script_options_kwarg_passes_the_keyword_for_non_ja(language):
    """Without this the ids would be silently dead — the plan's known failure."""
    options = frozenset({"hangul_only"})

    assert script_options_kwarg(options, language) == {"enabled_options": options}


def test_non_ja_options_reach_the_injected_script(test_config):
    """End to end: a splatted non-ja kwarg decides survivors via that script."""

    class _DropsShortForms:
        def filter_options(self):
            raise AssertionError("the call site derives options, the filter does not")

        def matches(self, option_id, form):
            return option_id == "short_form" and len(form) <= 3

        def contains_target_script(self, text):  # pragma: no cover - unused here
            return True

    service = WordFilterService(test_config, script=_DropsShortForms())
    kwargs = script_options_kwarg(frozenset({"short_form"}), "ko")

    kept = service.filter_by_script_type(_corpus(), **kwargs)

    # ぜんぶ and サボる are 3 chars, 学校 is 2 — only コーヒー (4) survives.
    assert [w.mined_form for w in kept] == ["コーヒー"]
