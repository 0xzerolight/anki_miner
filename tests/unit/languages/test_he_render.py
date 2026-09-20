"""The Hebrew card fields, read off REAL wty-he-en definitions rendered by the app's own path.

The definition HTML each hook sees is what ``IndexedDictProvider.lookup`` produces for a real
headword, so the regexes are held to the markup that actually ships, not to a hand-written sample.
Every Hebrew word is built from named Unicode characters; no literal is written here.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.he.render import (
    HE_EXTRA_CARD_FIELDS,
    HE_RENDER_HOOKS,
    HebrewGrammarHook,
    HebrewPosHook,
    HebrewRootHook,
    binyan,
    gender,
    head_line,
    noun_plural,
    root,
    transliteration,
)
from anki_miner.languages.he.script import GERESH, HebrewDictKeys
from anki_miner.services.dictionary import storage
from anki_miner.services.dictionary.importers.yomitan_importer import render_glossary_entry
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider
from anki_miner.services.dictionary.storage import DictRow, TagMeta

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "he"
WTY = json.loads((FIXTURES / "wty_rows.json").read_text(encoding="utf-8"))
CONFIG = AnkiMinerConfig()


def _word(*letters: str) -> str:
    return "".join(unicodedata.lookup(f"HEBREW LETTER {name}") for name in letters)


KELEV = _word("KAF", "LAMED", "BET")
HALAKH = _word("HE", "LAMED", "FINAL KAF")
NISHLACH = _word("NUN", "SHIN", "LAMED", "HET")
KARA_ROOT = _word("QOF", "RESH", "AYIN")
SEFER = _word("SAMEKH", "PE", "RESH")
ZAYIN_GERESH = _word("ZAYIN") + GERESH
NUN_GERESH = _word("NUN") + GERESH


def _rows():
    for term, _reading, definition_tags, rules, score, glossary, sequence, term_tags in WTY["term_rows"]:
        tags = [t for t in str(definition_tags).split(" ") if t]
        yield DictRow(
            term=term,
            reading="",
            content=render_glossary_entry(
                glossary if isinstance(glossary, list) else [glossary],
                definition_tags=tags,
                dict_id="wty-he-en",
                media_collector=None,
            ),
            tags=" ".join(tags + [t for t in str(term_tags).split(" ") if t]),
            rules=str(rules or ""),
            score=int(score or 0),
            sequence=int(sequence) if sequence is not None else None,
        )


@pytest.fixture(scope="module")
def lookup(tmp_path_factory):
    """``IndexedDictProvider.lookup`` over the committed subset: real card definition HTML."""
    db_path = tmp_path_factory.mktemp("wty_he_render") / "index.sqlite"
    storage.create_index(db_path)
    storage.write_meta(db_path, {"schema_version": str(storage.SCHEMA_VERSION), "source_name": "wty-he-en"})
    storage.bulk_insert(db_path, _rows(), keys=HebrewDictKeys())
    storage.write_tags(
        db_path,
        [TagMeta(name=row[0], category=row[1], ord=row[2], notes=row[3], score=row[4]) for row in WTY["tag_bank"]],
    )
    provider = IndexedDictProvider("wty-he-en", db_path, keys=HebrewDictKeys())
    assert provider.load()
    try:
        yield provider.lookup
    finally:
        provider.close()


class _Word:
    def __init__(self, definition_html: str, pos: str = "NOUN") -> None:
        self.definition_html = definition_html
        self.pos = pos
        self.mined_form = ""


# --------------------------------------------------------------------------
# The head-line readers, against real rendered entries
# --------------------------------------------------------------------------


def test_a_real_noun_entry_carries_a_head_line(lookup):
    assert head_line(lookup(KELEV))


def test_the_transliteration_is_the_dictionarys_own_first_spelling(lookup):
    assert transliteration(lookup(KELEV)).startswith("k")
    assert "," not in transliteration(lookup(KELEV))


def test_a_verb_entry_names_its_binyan(lookup):
    assert binyan(lookup(NISHLACH)) == "nif'al"


def test_a_noun_entry_has_no_binyan(lookup):
    assert binyan(lookup(KELEV)) == ""


def test_the_plural_is_the_form_and_not_the_word_indefinite(lookup):
    """The shared _spaced rule stops one word early and captures "indefinite"."""
    from anki_miner.languages._spaced.grammar_hook import _PLURAL_RE

    line = head_line(lookup(KELEV))
    shared = _PLURAL_RE.search(line)
    assert shared is not None and shared.group(1) == "indefinite"
    assert noun_plural(lookup(KELEV)) != "indefinite"
    assert all(unicodedata.category(char).startswith(("L", "M")) for char in noun_plural(lookup(KELEV)))


def test_the_gender_letter_is_read_after_the_romanisation(lookup):
    assert gender(lookup(KELEV)) == "m"


def test_a_root_is_read_out_of_the_etymology_block(lookup):
    found = root(lookup(KARA_ROOT))
    assert found and found.count("\N{HEBREW PUNCTUATION MAQAF}") == 2


def test_an_entry_without_a_root_gives_nothing(lookup):
    assert root(lookup(KELEV)) == ""


def test_every_reader_is_empty_on_empty_input():
    for reader in (head_line, transliteration, binyan, noun_plural, gender, root):
        assert reader("") == ""
        assert reader("<p>no grammar block here</p>") == ""


# --------------------------------------------------------------------------
# The hooks
# --------------------------------------------------------------------------


def test_a_noun_renders_transliteration_gender_and_plural(lookup):
    out = HebrewGrammarHook().render(_Word(lookup(KELEV), "NOUN"), config=CONFIG)
    assert out["noun_gender"] == ZAYIN_GERESH
    assert out["transliteration"]
    assert out["noun_plural"]
    assert "binyan" not in out


def test_a_verb_renders_its_binyan_and_no_noun_fields(lookup):
    out = HebrewGrammarHook().render(_Word(lookup(NISHLACH), "VERB"), config=CONFIG)
    assert out["binyan"] == "nif'al"
    assert "noun_gender" not in out
    assert "noun_plural" not in out


def test_a_feminine_noun_renders_the_feminine_label(lookup):
    """Read from the data: the first fem noun in the subset with a head-line gender letter."""
    candidates = [
        row[0]
        for row in WTY["term_rows"]
        if "non-lemma" not in str(row[2]).split(" ") and "fem" in str(row[2]).split(" ")
    ]
    for term in candidates:
        definition = lookup(term)
        if definition and gender(definition) == "f":
            out = HebrewGrammarHook().render(_Word(definition, "NOUN"), config=CONFIG)
            assert out["noun_gender"] == NUN_GERESH
            return
    pytest.fail("no feminine noun with a head-line gender letter in the fixture")


def test_an_unresolved_word_renders_nothing(lookup):
    assert HebrewGrammarHook().render(_Word("", "WORD"), config=CONFIG) == {}
    assert HebrewRootHook().render(_Word("", "WORD"), config=CONFIG) == {}


def test_the_root_hook_emits_only_what_the_dictionary_spells_out(lookup):
    assert HebrewRootHook().render(_Word(lookup(KARA_ROOT)), config=CONFIG)["root"]
    assert HebrewRootHook().render(_Word(lookup(KELEV)), config=CONFIG) == {}


@pytest.mark.parametrize(
    ("pos", "label"),
    [("NOUN", "noun"), ("VERB", "verb"), ("PROPN", "proper noun"), ("WORD", "word (unresolved)")],
)
def test_the_pos_hook_labels_the_resolved_part_of_speech(pos, label):
    assert HebrewPosHook().render(_Word("", pos), config=CONFIG) == {"pos": label}


def test_the_pos_hook_is_silent_for_an_unknown_pos():
    assert HebrewPosHook().render(_Word("", "nonsense"), config=CONFIG) == {}


# --------------------------------------------------------------------------
# The contract the language suite enforces
# --------------------------------------------------------------------------


def test_the_specs_and_the_hooks_name_exactly_the_same_keys():
    spec_keys = {spec.key for spec in HE_EXTRA_CARD_FIELDS}
    hook_keys = {name for hook in HE_RENDER_HOOKS for name in hook.field_names()}
    assert spec_keys == hook_keys


def test_every_hook_takes_the_config_keyword_only():
    import inspect

    for hook in HE_RENDER_HOOKS:
        parameter = inspect.signature(hook.render).parameters["config"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, type(hook).__name__


def test_the_root_field_reuses_the_shared_key_and_capability():
    [spec] = [spec for spec in HE_EXTRA_CARD_FIELDS if spec.key == "root"]
    assert spec.capability == "word_root"
    assert spec.placeholder == "Root"


def test_the_sefer_entry_is_a_noun_so_the_fixture_covers_both_paths(lookup):
    assert head_line(lookup(SEFER))
    assert head_line(lookup(HALAKH))
