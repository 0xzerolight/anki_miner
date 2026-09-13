"""_spaced POS labels, the POS hook, the extra card-field specs and the content style."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.fields import (
    NOUN_ARTICLE_FIELD,
    NOUN_GENDER_FIELD,
    NOUN_PLURAL_FIELD,
    POS_FIELD,
    spaced_card_fields,
)
from anki_miner.languages._spaced.pos import UPOS_ALLOWED, UPOS_LABELS
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.style import SPACED_CONTENT_STYLE

UPOS = {"ADJ", "ADP", "ADV", "AUX", "CCONJ", "DET", "INTJ", "NOUN", "NUM", "PART", "PRON", "PROPN", "PUNCT",
        "SCONJ", "SYM", "VERB", "X"}  # fmt: skip


def test_the_label_table_covers_universal_pos_exactly():
    assert set(UPOS_LABELS) == UPOS
    assert set(UPOS_ALLOWED) <= set(UPOS_LABELS)
    assert UPOS_ALLOWED == ("ADJ", "ADV", "NOUN", "VERB")


def test_pos_hook_renders_the_lowercase_label():
    hook = PosHook()
    assert hook.field_names() == ("pos",)
    assert hook.render(SimpleNamespace(pos="VERB"), config=AnkiMinerConfig()) == {"pos": "verb"}
    assert hook.render(SimpleNamespace(pos="PROPN"), config=AnkiMinerConfig()) == {"pos": "proper noun"}


def test_pos_hook_writes_nothing_for_an_unknown_tag():
    assert PosHook().render(SimpleNamespace(pos="名詞"), config=AnkiMinerConfig()) == {}
    assert PosHook().render(SimpleNamespace(pos=None), config=AnkiMinerConfig()) == {}


def test_pos_hook_takes_config_keyword_only():
    assert inspect.signature(PosHook().render).parameters["config"].kind is inspect.Parameter.KEYWORD_ONLY


def test_the_four_specs_name_their_capabilities():
    assert [
        (s.key, s.capability, s.raw_html) for s in (POS_FIELD, NOUN_GENDER_FIELD, NOUN_ARTICLE_FIELD, NOUN_PLURAL_FIELD)
    ] == [
        ("pos", "pos_tag", False),
        ("noun_gender", "noun_gender", False),
        ("noun_article", "noun_article", False),
        ("noun_plural", "noun_plural", False),
    ]


def test_card_fields_derive_from_the_config_default_with_furigana_blanked():
    fields = spaced_card_fields((POS_FIELD,))
    default = dict(AnkiMinerConfig().anki_fields)
    assert set(fields) == set(default) | {"pos"}
    assert fields["expression_furigana"] == "" and fields["sentence_furigana"] == ""
    assert fields["pos"] == ""
    assert fields["word"] == default["word"]


def test_content_style_is_a_non_japanese_role_with_faces():
    assert SPACED_CONTENT_STYLE.font_role == "latin"
    assert SPACED_CONTENT_STYLE.families
    assert SPACED_CONTENT_STYLE.wrap("well-known words") == "well-known words"
