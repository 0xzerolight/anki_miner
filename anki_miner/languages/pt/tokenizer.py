"""Portuguese tokenizer: ``pt_core_news_sm`` through the shared spaCy adapter, with hyphen enclisis split.

The pipeline comes from ``build_spacy_tagger`` (the shared dash infix, and the
word-dot exceptions pruned against ``PT_ABBREVIATIONS`` so ``dom.`` at a
sentence end is the word ``dom``). The model's own infix keeps hyphenated
compounds whole (``guarda-chuva``, ``bem-te-vi``). It also keeps a verb and its
enclitic pronoun as one token (``Dá-me``, ``levantou-se``) with no usable
lemma, and no post-pass can repair a lemma the model never produced, so
``PortugueseTagger`` tags ``enclitic_copy(tagging_copy(text))`` — the same
length, the hyphen before a closing clitic replaced by a space — and slices
every surface from the original line: the host (``Dá``) and the clitic
(``me``) are verbatim substrings. The split-off clitic is a pronoun whatever
the model says, a ``-lo/-la`` host takes the infinitive its spelling encodes,
and ``PT_POST_PASSES`` then run like any language's ``post_passes``. No parser:
Portuguese has no separable verbs, and dropping it changes no token, POS, lemma
or morph (probed).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from anki_miner.languages._spaced.morphology import tagging_copy
from anki_miner.languages._spaced.tokenizer import TokenPass, build_spacy_tagger
from anki_miner.languages._spaced.tokens import to_duck_tokens
from anki_miner.languages.pt.morphology import (
    L_CLITICS,
    PT_ABBREVIATIONS,
    PT_MODEL_PACKAGE,
    PT_POST_PASSES,
    enclitic_copy,
    infinitive_before_l_clitic,
)
from anki_miner.languages.token import LanguageToken
from anki_miner.services.tagger import LockedTagger


class PortugueseTagger:
    """Callable with the fugashi tagger contract: ``tagger(text) -> list[LanguageToken]``."""

    def __init__(self, nlp: Any, *, post_passes: Sequence[TokenPass] = ()) -> None:
        self.nlp = nlp
        self._post_passes = tuple(post_passes)

    def __call__(self, text: str, **_: Any) -> list[LanguageToken]:
        copy, clitic_starts = enclitic_copy(tagging_copy(text))
        doc = self.nlp(copy)
        tokens = to_duck_tokens(doc, text)
        kept = [tok for tok in doc if not tok.is_space]  # to_duck_tokens' own order
        for index, (tok, token) in enumerate(zip(kept, tokens, strict=True)):
            if tok.idx not in clitic_starts:
                continue
            token.feature.pos1 = "PRON"
            if index and token.surface.lower() in L_CLITICS:
                host = tokens[index - 1]
                infinitive = infinitive_before_l_clitic(host.surface)
                if infinitive is not None:
                    host.feature.lemma = infinitive
                    host.feature.pos1 = "VERB"
        for post_pass in self._post_passes:
            tokens = post_pass(tokens)
        return tokens

    def parse(self, text: str) -> list[LanguageToken]:
        """fugashi-compatible alias so ``LockedTagger.parse`` delegates cleanly."""
        return self(text)


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point: the shared adapter's configured pipeline, tagged the Portuguese way.

    ``build_spacy_tagger`` loads the model once and configures its tokenizer;
    only its public ``.nlp`` is kept (the enclitic split must reach the copy the
    model reads, which a ``TokenPass`` cannot).
    """
    configured = build_spacy_tagger(PT_MODEL_PACKAGE, abbreviations=PT_ABBREVIATIONS)
    return LockedTagger(PortugueseTagger(configured.nlp, post_passes=PT_POST_PASSES))
