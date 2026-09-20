"""GrammarTagHook(head_fold=...) and drop_romanisation (plan D10): wty's romanised Cyrillic head lines.

wty-ru-en prints a romanisation where the head-line rules expect the headword to end, so without the
fold the rung finds nothing (1 gender in 28,308 noun lines). The fold is opt-in: el pins its head-line
rung inert (test_el_wty_row.py).
"""

from __future__ import annotations

from types import SimpleNamespace

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook, drop_romanisation

A = "\N{COMBINING ACUTE ACCENT}"
VERB = f"чита{A}ть • (čitátʹ) impf " f"(perfective прочита{A}ть or почита{A}ть or проче{A}сть, verbal noun чте{A}ние)"
VERB_FOLDED = f"чита{A}ть impf (perfective прочита{A}ть or почита{A}ть or проче{A}сть, verbal noun чте{A}ние)"
NOUN = f"кни{A}га • (kníga) f inan (genitive кни{A}ги, nominative plural кни{A}ги, genitive plural книг)"
NESTED = f"вега{A}н^* or ве{A}ган^(**) • (vegán^* or végan^(**)) m anim (genitive вега{A}на^*)"


def _render(hook: GrammarTagHook, pos: str, head: str, morph: str = "") -> dict[str, str]:
    html = f'<li data-dictionary="wty-ru-en"><div data-sc-content="Grammar-content">{head}</div></li>'
    word = SimpleNamespace(pos=pos, morph=morph, definition_html=html, mined_form="")
    return hook.render(word, config=AnkiMinerConfig())


def test_drop_romanisation_removes_the_first_bullet_clause_only():
    assert drop_romanisation(VERB) == VERB_FOLDED
    assert drop_romanisation(drop_romanisation(VERB)) == VERB_FOLDED
    assert drop_romanisation("stół m inan (genitive stołu)") == "stół m inan (genitive stołu)"
    assert drop_romanisation(NESTED) == NESTED  # a bracketed romanisation is left alone


def test_without_the_fold_a_romanised_head_line_answers_nothing():
    hook = GrammarTagHook(("noun_gender", "aspect_pair"))
    assert _render(hook, "VERB", VERB) == {}
    assert _render(hook, "NOUN", NOUN) == {}


def test_the_fold_reads_aspect_partner_and_gender():
    hook = GrammarTagHook(("noun_gender", "aspect_pair"), head_fold=drop_romanisation)
    assert _render(hook, "VERB", VERB) == {
        "aspect_pair": f"imperfective (perfective: прочита{A}ть or почита{A}ть or проче{A}сть)"
    }
    assert _render(hook, "NOUN", NOUN) == {"noun_gender": "feminine"}


def test_morph_still_wins_over_the_folded_head_line():
    hook = GrammarTagHook(("noun_gender",), head_fold=drop_romanisation)
    assert _render(hook, "NOUN", NOUN, morph="Animacy=Inan|Case=Nom|Gender=Masc|Number=Sing") == {
        "noun_gender": "masculine"
    }
