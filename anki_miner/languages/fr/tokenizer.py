"""French tokenizer: ``fr_core_news_sm`` through the shared spaCy adapter, with French rules.

``build_spacy_tagger`` does the shared work: curly apostrophes folded in the
tagging copy (the model mis-tags ``n’``/``s’``/``l’`` as VERB/NOUN), the base
letter-hyphen-letter infix dropped (``join_hyphenated``: stock fr splits
``week-end``, ``tee-shirt``, ``sang-froid``, ``moi-même``), the dash infix,
dotted abbreviations as ``X`` and pruned single-dot exceptions. French adds, on
the built tokenizer (``customise_tokenizer``):

* a suffix rule splitting a hyphen + clitic pronoun (``Donne-le-moi`` →
  ``Donne`` ``-le`` ``-moi``, ``Viens-tu`` → ``Viens`` ``-tu``), extending the
  model's shorter list;
* ``token_match`` skipped for a string ending in hyphen + clitic: its
  ``saut-de-ski`` rule otherwise keeps ``Donne-le-moi`` as one NOUN;
* no elision split right after ``<letter>-d'`` or ``<letter>-l'``: with the
  hyphen infix gone the elision infix alone would cut ``chef-d'oeuvre``,
  ``main-d'œuvre`` and ``trompe-l'œil`` into ``chef-d'`` + ``oeuvre``;
* special cases for ``FR_WHOLE_WORDS``.

``retag_french_tokens`` (a tokenizer post-pass) fixes the closed classes the
model gets wrong. Surfaces are always slices of the original line.
"""

from __future__ import annotations

import re
from typing import Any

from anki_miner.languages._spaced.morphology import APOSTROPHE_FOLD
from anki_miner.languages._spaced.tokenizer import build_spacy_tagger
from anki_miner.languages.fr.morphology import (
    FR_ABBREVIATIONS,
    FR_CLITIC_PRONOUNS,
    FR_FIXED_TOKENS,
    FR_MODEL_PACKAGE,
    FR_TITLE_ABBREVIATIONS,
    FR_WHOLE_WORDS,
    fold_apostrophes,
)
from anki_miner.languages.token import LanguageToken
from anki_miner.services.tagger import LockedTagger

#: Word-joining hyphens (ASCII, U+2010, U+2011). En and em dashes are dialogue
#: dashes, never a clitic joint.
_WORD_HYPHENS = "-‐‑"
_CLITICS = "|".join(sorted(FR_CLITIC_PRONOUNS, key=len, reverse=True))
_CLITIC_TOKEN = re.compile(rf"[{_WORD_HYPHENS}](?:{_CLITICS})")
_ENDS_IN_CLITIC = re.compile(rf"(?i)[{_WORD_HYPHENS}](?:{_CLITICS})$")
#: Text before a zero-width elision split that ends in "<letter>-d'" or "<letter>-l'" (the tagging copy is ASCII).
_HYPHEN_ELISION = re.compile(rf"[^\W\d_][{_WORD_HYPHENS}][dDlL]'$")


def customise_tokenizer(nlp: Any) -> None:
    """Add the French clitic split, the clitic-chain ``token_match`` skip, the hyphen-elision guard and special cases.

    Runs on the tokenizer ``build_spacy_tagger`` already configured: it replaces
    only ``suffix_search`` (the adapter never touches suffixes), wraps
    ``token_match`` and ``infix_finditer`` (dropping one kind of match, so the
    adapter's dash infix survives) and adds special cases after the rules prune,
    so none of the shared configuration is undone.
    """
    from spacy.lang.char_classes import ALPHA
    from spacy.util import compile_suffix_regex

    clitic_suffix = rf"(?<=[{ALPHA}])[{_WORD_HYPHENS}](?i:{_CLITICS})"
    tok = nlp.tokenizer
    tok.suffix_search = compile_suffix_regex([*nlp.Defaults.suffixes, clitic_suffix]).search
    stock_match = tok.token_match
    if stock_match is not None:

        def token_match(text: str) -> Any:
            return None if _ENDS_IN_CLITIC.search(text) else stock_match(text)

        tok.token_match = token_match
    stock_infixes = tok.infix_finditer

    def infix_finditer(text: str) -> list[Any]:
        return [
            match
            for match in stock_infixes(text)
            if not (match.start() == match.end() and _HYPHEN_ELISION.search(text, 0, match.start()))
        ]

    tok.infix_finditer = infix_finditer
    for orth in FR_WHOLE_WORDS:
        for variant in dict.fromkeys((orth, orth[:1].upper() + orth[1:])):
            tok.add_special_case(variant, [{"ORTH": variant}])


def retag_french_tokens(tokens: list[LanguageToken]) -> list[LanguageToken]:
    """A ``TokenPass``: fix the closed classes ``fr_core_news_sm`` mis-tags, in place.

    Hyphen clitics → PRON (lemma without the hyphen); undotted title
    abbreviations → X; ``FR_FIXED_TOKENS`` → their POS and headword; ADV ``ne``
    → PART (UD French tags the negation particle ADV; en's ``not`` is PART).
    Table lookups, never capitalisation heuristics (§2).
    """
    for token in tokens:
        feature = token.feature
        key = fold_apostrophes(token.surface).casefold()
        if _CLITIC_TOKEN.fullmatch(key):
            feature.pos1, feature.lemma = "PRON", key[1:]
        elif token.surface.rstrip(".") in FR_TITLE_ABBREVIATIONS:
            feature.pos1 = "X"
        elif key in FR_FIXED_TOKENS:
            feature.pos1, feature.lemma = FR_FIXED_TOKENS[key]
        elif feature.pos1 == "ADV" and feature.lemma == "ne":
            feature.pos1 = "PART"
    return tokens


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point; no parser (no separable verbs)."""
    tagger = build_spacy_tagger(
        FR_MODEL_PACKAGE,
        join_hyphenated=True,
        tag_char_map=APOSTROPHE_FOLD,
        post_passes=(retag_french_tokens,),
        abbreviations=FR_ABBREVIATIONS,
    )
    customise_tokenizer(tagger.nlp)
    return tagger
