"""Spanish tokenizer: ``es_core_news_sm`` through the shared spaCy adapter, plus a verb-lemma repair post-pass.

The model's rule lemmatizer follows the morphologizer's features, and the small
model often gets them wrong: ``dámelo`` → ``dámelir``, ``levantarme`` →
``levantarmar``, ``dijiste`` → ``dijistir``, ``riendo`` → ``reir``; when it does
see the pronouns it returns a multi-word lemma (``levantarse`` → ``levantar él``),
and many clitic verbs arrive tagged NOUN, ADJ or PROPN (``sentarnos``). Such a
front is junk on the card, in dedup and known-word keys and in the frequency
rank. ``SpanishVerbRepair`` re-derives the lemma from the SAME model's tables —
the verb ``lemma_index`` (attested infinitives), ``select_rule`` and
``lemmatize_verb`` — and replaces it only with an attested infinitive (the
model's accentless -eír/-oír lemmas ``reir``, ``oir``, ``freir``, ``sonreir``,
``desoir`` resolve to the accented infinitive). Measured against wty-es-en's
form-of rows over the OpenSubtitles 50k list: 5,021 fronts right, 33 wrong;
everything the rules cannot decide keeps the model's lemma.

It runs as a ``build_spacy_tagger`` post-pass, so every tagger call — the parser,
the word filter, Card Backfill, the frequency lemmatiser — sees repaired fronts.
Dotted abbreviations (``Sra.``, ``a.m``) are already ``X`` from the shared
duck-token rule; curly apostrophes are folded in the tagging copy only (``na’``
tags like ``na'``); Spanish compounds (``franco-alemán``) are one token already.
"""

from __future__ import annotations

from typing import Any

from anki_miner.languages._spaced.morphology import APOSTROPHE_FOLD
from anki_miner.languages._spaced.tokenizer import build_spacy_tagger
from anki_miner.languages.es.morphology import (
    ES_ABBREVIATIONS,
    ES_MODEL_PACKAGE,
    IRREGULAR_IMPERATIVES,
    enclitic_splits,
    has_acute,
    strip_acute,
)
from anki_miner.languages.token import LanguageToken
from anki_miner.services.tagger import LockedTagger

_VERB_POS = frozenset({"VERB", "AUX"})
_NOMINAL_POS = frozenset({"NOUN", "ADJ", "PROPN"})
_INFINITIVE_ENDINGS = ("ar", "er", "ir")
_GERUND = ["VerbForm=Ger"]
_IMPERATIVE_2SG = ["Mood=Imp", "Number=Sing", "Person=2", "VerbForm=Fin"]
#: Distinct (tag, surface, lemma) triples remembered before the memo starts over.
_MEMO_LIMIT = 50_000


class SpanishVerbRepair:
    """A ``TokenPass``: attested-infinitive fronts for verbs the small model mis-lemmatises (es plan D1)."""

    def __init__(self, lemmatizer: Any | None = None) -> None:
        self._lemmatizer: Any | None = None
        self._infinitives: frozenset[str] = frozenset()
        self._accented: dict[str, str] = {}
        self._groups: tuple[tuple[str, list[str]], ...] = ()
        self._memo: dict[tuple[str, str, str], tuple[str, str]] = {}
        if lemmatizer is not None:
            self.bind(lemmatizer)

    def bind(self, lemmatizer: Any) -> None:
        """Read the tables of the loaded model's ``lemmatizer`` pipe (``build_tagger`` binds before first use)."""
        tables = lemmatizer.lookups
        infinitives = frozenset(tables.get_table("lemma_index").get("verb", []))
        self._lemmatizer = lemmatizer
        self._infinitives = infinitives
        # reír, oír, freír, sonreír, desoír: the model lemmatises them without the accent.
        self._accented = {
            strip_acute(inf): inf
            for inf in infinitives
            if inf != strip_acute(inf) and strip_acute(inf).endswith(_INFINITIVE_ENDINGS)
        }
        self._groups = tuple(
            (name, list(features)) for name, features in tables.get_table("lemma_rules_groups").get("verb", [])
        )
        self._memo.clear()

    def __call__(self, tokens: list[LanguageToken]) -> list[LanguageToken]:
        if self._lemmatizer is None:
            raise RuntimeError("SpanishVerbRepair used before bind()")
        for token in tokens:
            feature = token.feature
            feature.pos1, feature.lemma = self.repair(feature.pos1, token.surface, feature.lemma)
        return tokens

    def repair(self, pos: str, surface: str, lemma: str) -> tuple[str, str]:
        """``(tag, front)`` for one token; unchanged unless an attested infinitive is certain."""
        key = (pos, surface, lemma)
        cached = self._memo.get(key)
        if cached is None:
            if len(self._memo) >= _MEMO_LIMIT:
                self._memo.clear()
            cached = self._memo[key] = self._repair(pos, surface, lemma)
        return cached

    def _attested(self, candidate: str) -> str | None:
        return candidate if candidate in self._infinitives else self._accented.get(candidate)

    def _repair(self, pos: str, surface: str, lemma: str) -> tuple[str, str]:
        if pos in _VERB_POS:
            head = lemma.split(" ", 1)[0]
            shaped = self._infinitive_or_gerund(surface)
            if shaped is not None:
                return pos, shaped
            attested = self._attested(head)
            if attested is not None:
                return pos, attested
            repaired = self._imperative_of(surface, nominal=False) or self._unique_rule_lemma(surface)
            return pos, repaired or head
        if pos in _NOMINAL_POS:
            repaired = self._infinitive_or_gerund(surface) or self._imperative_of(surface, nominal=True)
            if repaired is not None:
                return "VERB", repaired
        return pos, lemma

    def _rule_hits(self, form: str, features: list[str], rule: str | None) -> list[str]:
        assert self._lemmatizer is not None
        hits: list[str] = []
        for candidate in self._lemmatizer.lemmatize_verb(form, features, rule, self._infinitives):
            attested = self._attested(candidate)
            if attested is not None and attested not in hits:
                hits.append(attested)
        return hits

    def _infinitive_or_gerund(self, surface: str) -> str | None:
        assert self._lemmatizer is not None
        for stem, chain in enclitic_splits(surface.casefold()):
            base = strip_acute(stem)
            accented = has_acute(stem)
            if base.endswith(_INFINITIVE_ENDINGS):
                infinitive = self._attested(base)
                # A written accent comes with two clitics (dármelo) or is the verb's own (oírme).
                if infinitive is not None and (not accented or len(chain) >= 2 or infinitive == stem):
                    return infinitive
            if base.endswith("ndo") and accented:
                hits = self._rule_hits(base, _GERUND, self._lemmatizer.select_rule("verb", _GERUND))
                if hits:
                    return hits[0]
        return None

    def _imperative_of(self, surface: str, *, nominal: bool) -> str | None:
        assert self._lemmatizer is not None
        for stem, chain in enclitic_splits(surface.casefold()):
            base = strip_acute(stem)
            if not has_acute(stem):
                if nominal:
                    continue
                irregular = IRREGULAR_IMPERATIVES.get(base)
                if irregular is not None:
                    return irregular
                continue
            if nominal and len(chain) < 2 and chain[0] not in ("me", "te", "se"):
                continue
            hits = self._rule_hits(base, _IMPERATIVE_2SG, self._lemmatizer.select_rule("verb", _IMPERATIVE_2SG))
            if len(hits) > 1:
                # tú + te: siéntate (-a stem) is sentar; usted + se: siéntese (-e stem) is sentar too.
                usted = chain[0] == "se" and "te" not in chain and "os" not in chain
                wanted = ("er", "ir") if base.endswith("a") == usted else ("ar",)
                hits = [hit for hit in hits if hit.endswith(wanted)] or hits
            if hits:
                return hits[0]
        return None

    def _unique_rule_lemma(self, surface: str) -> str | None:
        form = surface.casefold()
        for candidate in dict.fromkeys((form, strip_acute(form))):
            hits: list[str] = []
            for rule, features in self._groups:
                for hit in self._rule_hits(candidate, features, rule):
                    if hit not in hits:
                        hits.append(hit)
            if hits:
                return hits[0] if len(hits) == 1 else None
        return None


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point: no parser (no separable verbs; tags are identical without it).

    The repair needs the loaded model's lemmatizer, which ``build_spacy_tagger``
    creates, so it is bound right after the build and before the tagger is returned
    (es plan D16) — ``build_spacy_tagger`` keeps the dash infix and the abbreviation
    pruning a hand-built ``SpacyTagger`` would skip.
    """
    repair = SpanishVerbRepair()
    tagger = build_spacy_tagger(
        ES_MODEL_PACKAGE,
        tag_char_map=APOSTROPHE_FOLD,
        post_passes=(repair,),
        abbreviations=ES_ABBREVIATIONS,
    )
    repair.bind(tagger.nlp.get_pipe("lemmatizer"))
    return tagger
