"""Tests for ``grammar_tags``: dictionary grammar tags encoded as one envelope attribute and read back."""

from __future__ import annotations

from anki_miner.services.dictionary.grammar_tags import GrammarTags, encode_grammar_tags, parse_grammar_attr
from anki_miner.services.dictionary.storage import TagMeta


def test_encode_filters_to_grammar_tags():
    metas = [
        TagMeta("f", "gender-feminine", 0, "feminine", 0),
        TagMeta("pn", "partOfSpeech", -3, "pronoun", 0),
        TagMeta("ipf", "", 0, "imperfective", 0),
        TagMeta("⭐", "popular", -10, "high priority", 10),
    ]
    assert encode_grammar_tags(metas) == "aspect:impf gender:feminine"


def test_encode_empty_for_jmdict_style_tags():
    assert (
        encode_grammar_tags([TagMeta("n", "partOfSpeech", -3, "noun", 0), TagMeta("uk", "", 0, "usually kana", 0)])
        == ""
    )


def test_parse_reads_first_block_only():
    html = (
        '<li data-dictionary="A" data-dictionary-id="a" data-am-grammar="gender:neuter aspect:pf">…</li>'
        '<li data-dictionary="B" data-dictionary-id="b" data-am-grammar="gender:feminine">…</li>'
    )
    assert parse_grammar_attr(html) == GrammarTags(genders=frozenset({"neut"}), aspects=frozenset({"pf"}))


def test_parse_absent_attribute():
    assert parse_grammar_attr('<li data-dictionary="A" data-dictionary-id="a">x</li>') == GrammarTags(
        frozenset(), frozenset()
    )
