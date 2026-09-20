"""The Persian card hooks (spec C.2): Romanization, Colloquial form and Present stem.

The head line is the shape the Yomitan importer renders for wty-fa-en (``Grammar-content``,
copied from real rows); every romanisation below is a verbatim wty capture, counted in probe
P-7 and re-counted over the six term banks while this task was written. Every Persian
character and every accented Latin one is a \\N{NAME} escape (LEAD-BRIEF section 3): a
decomposed circumflex would make these pass or fail for the wrong reason.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.fa.morphology import FA_MORPH_INFORMAL, fa_morph, fa_morph_value
from anki_miner.languages.fa.render import (
    FA_RENDER_HOOKS,
    PersianRegisterHook,
    PersianRomanizationHook,
    PersianStemHook,
    romanization,
)

BULLET = "\N{BULLET}"
A_CIRC = "\N{LATIN SMALL LETTER A WITH CIRCUMFLEX}"
A_MACRON = "\N{LATIN SMALL LETTER A WITH MACRON}"
E_MACRON = "\N{LATIN SMALL LETTER E WITH MACRON}"
I_MACRON = "\N{LATIN SMALL LETTER I WITH MACRON}"
O_CIRC = "\N{LATIN SMALL LETTER O WITH CIRCUMFLEX}"
EMPTY_SET = "\N{EMPTY SET}"

_ALEF = "\N{ARABIC LETTER ALEF}"
_BEH = "\N{ARABIC LETTER BEH}"
_TEH = "\N{ARABIC LETTER TEH}"
_REH = "\N{ARABIC LETTER REH}"
_FEH = "\N{ARABIC LETTER FEH}"
_KAF = "\N{ARABIC LETTER KAF}"
_KEHEH = "\N{ARABIC LETTER KEHEH}"
_LAM = "\N{ARABIC LETTER LAM}"
_NOON = "\N{ARABIC LETTER NOON}"
_WAW = "\N{ARABIC LETTER WAW}"
_HEH = "\N{ARABIC LETTER HEH}"
_KHAH = "\N{ARABIC LETTER KHAH}"

#: The head line's headword is never read; one real row's is enough (kalb, "dog").
HEADWORD = _KAF + _LAM + _BEH
XANE = _KHAH + _ALEF + _NOON + _HEH  # xane, "house" -- the formal spelling
XUNE = _KHAH + _WAW + _NOON + _HEH  # xune, the colloquial one
RAFTAN = _REH + _FEH + _TEH + _NOON  # raftan, "to go"
RO = _REH + _WAW  # its present stem
KETAB = _KEHEH + _TEH + _ALEF + _BEH


def _head(line: str) -> str:
    """One rendered ``Grammar-content`` block, the importer's own shape."""
    return (
        '<details class="gloss-sc-details" data-sc-content="details-entry-Grammar">'
        '<summary class="gloss-sc-summary" data-sc-content="summary-entry">Grammar</summary>'
        f'<div class="gloss-sc-div" data-sc-content="Grammar-content">{line}</div></details>'
        '<ol class="gloss-sc-ol" data-sc-content="glosses"><li class="gloss-sc-li">'
        '<div class="gloss-sc-div">a dog</div></li></ol>'
    )


def _grammar(romanisation: str, tail: str = "") -> str:
    return _head(f"{HEADWORD} {BULLET} ({romanisation}){tail}")


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # single (11,097 of the 16,965 head lines)
        ("kalb", "kalb"),
        # slash, spaced (4,244): the SECOND member is the Iranian spelling
        (f"hal{A_MACRON}l / hal{A_CIRC}l", f"hal{A_CIRC}l"),
        # slash, unspaced (1,235) -- the shape the plan's " / " split would have missed
        (f"ham{A_MACRON}s /ham{A_CIRC}s", f"ham{A_CIRC}s"),
        # comma (195): the FIRST member
        (f"{E_MACRON}n, {I_MACRON}n", f"{E_MACRON}n"),
        # " or " (193): the first alternative, then its own slash rule
        (f"{A_CIRC} or {EMPTY_SET}", A_CIRC),
        (f"naw{I_MACRON}d /navid or nuw{E_MACRON}d /-", "navid"),
        # a parenthesis INSIDE the romanisation (19 rows): a balanced scan, not [^)]+
        (f"{A_CIRC}b(-e) kir", f"{A_CIRC}b(-e) kir"),
        (f"{A_CIRC}r(e)zu or {A_CIRC}r(i)z{O_CIRC}", f"{A_CIRC}r(e)zu"),
    ],
)
def test_the_head_line_romanisation(line, expected):
    assert romanization(_grammar(line)) == expected


def test_a_trailing_parenthesis_group_is_not_the_romanisation():
    """Real rows append (plural ...), (comparative ...), (Tajik spelling ...)."""
    tail = f" (plural {KETAB}) (Tajik spelling {HEADWORD})"
    assert romanization(_grammar(f"hal{A_MACRON}l / hal{A_CIRC}l", tail)) == f"hal{A_CIRC}l"


def test_no_head_line_is_no_romanisation():
    assert romanization(_head(f"{HEADWORD} {BULLET}")) == ""
    assert romanization("<div>a dog</div>") == ""
    assert romanization("") == ""


def test_the_first_head_line_wins():
    first = _grammar("kalb")
    assert romanization(first + _grammar(f"ham{A_MACRON}s /ham{A_CIRC}s")) == "kalb"


class TestRomanizationHook:
    def test_it_fills_its_one_field(self):
        hook = PersianRomanizationHook()
        assert hook.field_names() == ("reading_romanized",)
        word = SimpleNamespace(definition_html=_grammar("kalb"), mined_form=HEADWORD, morph="")
        assert hook.render(word, config=AnkiMinerConfig()) == {"reading_romanized": "kalb"}

    def test_an_entry_with_no_head_line_writes_nothing(self):
        word = SimpleNamespace(definition_html="<div>a dog</div>", mined_form=HEADWORD, morph="")
        assert PersianRomanizationHook().render(word, config=AnkiMinerConfig()) == {}


class TestRegisterHook:
    def test_a_colloquial_front_names_the_spelling_the_subtitle_used(self):
        hook = PersianRegisterHook()
        assert hook.field_names() == ("colloquial_form",)
        word = SimpleNamespace(
            surface=XUNE,
            mined_form=XANE,
            definition_html="",
            morph=fa_morph(informal=True, present_stem=""),
        )
        assert hook.render(word, config=AnkiMinerConfig()) == {"colloquial_form": XUNE}

    def test_a_formal_token_writes_nothing(self):
        word = SimpleNamespace(surface=XANE, mined_form=XANE, definition_html="", morph="")
        assert PersianRegisterHook().render(word, config=AnkiMinerConfig()) == {}

    def test_a_colloquial_spelling_that_is_its_own_front_writes_nothing(self):
        word = SimpleNamespace(
            surface=XUNE,
            mined_form=XUNE,
            definition_html="",
            morph=fa_morph(informal=True, present_stem=""),
        )
        assert PersianRegisterHook().render(word, config=AnkiMinerConfig()) == {}


class TestStemHook:
    def test_a_verb_carries_its_present_stem(self):
        hook = PersianStemHook()
        assert hook.field_names() == ("present_stem",)
        word = SimpleNamespace(
            surface=RAFTAN, mined_form=RAFTAN, definition_html="", morph=fa_morph(informal=False, present_stem=RO)
        )
        assert hook.render(word, config=AnkiMinerConfig()) == {"present_stem": RO}

    def test_a_noun_writes_nothing(self):
        word = SimpleNamespace(surface=KETAB, mined_form=KETAB, definition_html="", morph="")
        assert PersianStemHook().render(word, config=AnkiMinerConfig()) == {}


class TestMorphChannel:
    """``morph`` is the only feature channel a TokenizedWord carries (pos2 is not one)."""

    def test_the_two_features_share_one_string(self):
        morph = fa_morph(informal=True, present_stem=RO)
        assert morph == f"{FA_MORPH_INFORMAL}|PresentStem={RO}"
        assert fa_morph_value(morph, "PresentStem") == RO

    def test_an_unset_feature_reads_empty(self):
        assert fa_morph(informal=False, present_stem="") == ""
        assert fa_morph_value("", "PresentStem") == ""
        assert fa_morph_value(FA_MORPH_INFORMAL, "PresentStem") == ""


def test_each_field_key_has_a_translatable_settings_row():
    """The row is what makes the key mappable; the profile rows join HOOK_ROWS at Task 14."""
    from anki_miner.gui.widgets.panels.anki_settings_panel import _HOOK_FIELD_ROW_TEXTS

    for key in ("reading_romanized", "colloquial_form", "present_stem"):
        label, helper = _HOOK_FIELD_ROW_TEXTS[key]
        assert label.endswith("Field"), key
        assert helper.endswith("Blank = skip."), key


def test_the_three_hooks_ship_together_and_take_config_keyword_only():
    import inspect

    assert [type(hook).__name__ for hook in FA_RENDER_HOOKS] == [
        "PersianRomanizationHook",
        "PersianRegisterHook",
        "PersianStemHook",
    ]
    keys = [name for hook in FA_RENDER_HOOKS for name in hook.field_names()]
    assert keys == ["reading_romanized", "colloquial_form", "present_stem"]
    for hook in FA_RENDER_HOOKS:
        assert inspect.signature(hook.render).parameters["config"].kind is inspect.Parameter.KEYWORD_ONLY
