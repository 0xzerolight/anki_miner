"""The pymorphy3 token post-pass shared by the Cyrillic spaCy languages (ru, uk).

``ru_core_news_sm`` and ``uk_core_news_sm`` both lemmatise through pymorphy3, and both need the same
two repairs over the duck tokens: a joined hyphenated token the model never saw as one word, and a
content token whose lemma is its own surface because no pymorphy3 parse matched the morphologizer's
features (``spacy/lang/ru/lemmatizer.py``: ``if not len(filtered_analyses): return [string.lower()]``).
The analyser is injected by ``bind``, so this module imports neither spaCy nor pymorphy3 and a
profile still builds on a machine without the engine.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from anki_miner.languages._spaced.morphology import case_lemma
from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages.token import LanguageToken

_HYPHENATED = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)+")

#: ``spacy.lang.ru.lemmatizer.oc2ud``: an OpenCorpora tag string -> (UPOS, features). uk's
#: dictionaries use the same tagset, and ``UkrainianLemmatizer`` subclasses ``RussianLemmatizer``.
ToUpos = Callable[[str], tuple[str, dict[str, str]]]


def _same(text: str) -> str:
    """The default ``analysis_form``: ask the analyser for the surface exactly as the line wrote it."""
    return text


class PymorphyLemmaRepair:
    """Tokenizer post-pass over the model's own pymorphy3 analyser, bound once the model is loaded.

    1. A joined hyphenated token (кто-то, по-українськи, інтернет-магазин) takes POS, lemma and
       morph from pymorphy3's first known parse: the model never saw these as one token and tags
       them at random (Когда-то as PUNCT, по-українськи as a feminine NOUN). An unknown one
       (диван-кровать) keeps the model's answer.
    2. A content token whose lemma is its own surface under ``fold`` takes the one normal form that
       pymorphy3's known parses of the same UPOS agree on (вяжет -> вязать, сховалася ->
       сховатися), else the lower-cased ``analysis_form`` of its surface.

    ``fold`` is what "the lemma is the surface" means for the language, and it exists because the
    tagging copy rewrites the surface before the model sees it: ``str.lower`` by default, yo-blind
    for ru (ё -> е), apostrophe-blind for uk. ``analysis_form`` is the spelling the analyser is
    asked for, in both branches, and the base of the ambiguous fallback: identity by default,
    because ru's dictionaries know its surfaces as written, and the U+0027 canonicaliser for uk,
    whose dictionaries know only that one apostrophe -- ask them about a typographic ``м'яча`` and
    every parse comes back ``is_known=False``, so the repair would fall back onto the inflected
    form it exists to fix. ``allowed_pos`` is the language's ``allowed_pos`` gate.

    Not repaired: a token whose POS no parse shares (дыша as NOUN), a participle tagged ADJ.
    """

    def __init__(
        self,
        *,
        allowed_pos: tuple[str, ...] = UPOS_ALLOWED,
        fold: Callable[[str], str] = str.lower,
        analysis_form: Callable[[str], str] = _same,
    ) -> None:
        self._allowed_pos = allowed_pos
        self._fold = fold
        self._analysis_form = analysis_form
        self._analyzer: Any = None
        self._to_upos: ToUpos | None = None

    def bind(self, analyzer: Any, to_upos: ToUpos) -> None:
        self._analyzer = analyzer
        self._to_upos = to_upos

    def __call__(self, tokens: list[LanguageToken]) -> list[LanguageToken]:
        if self._analyzer is None or self._to_upos is None:
            raise RuntimeError("PymorphyLemmaRepair runs only after bind()")
        for token in tokens:
            if _HYPHENATED.fullmatch(token.surface):
                self._retag(token, self._to_upos)
            elif token.feature.pos1 in self._allowed_pos and self._fold(token.feature.lemma) == self._fold(
                token.surface
            ):
                self._relemmatise(token, self._to_upos)
        return tokens

    def _known(self, word: str) -> list[Any]:
        return [parse for parse in self._analyzer.parse(word) if parse.is_known]

    def _retag(self, token: LanguageToken, to_upos: ToUpos) -> None:
        parses = self._known(self._analysis_form(token.surface))
        if not parses:
            return
        pos, features = to_upos(str(parses[0].tag))
        token.feature.pos1 = pos
        token.feature.lemma = case_lemma(parses[0].normal_form, pos)
        token.morph = "|".join(f"{name}={value}" for name, value in sorted(features.items()))

    def _relemmatise(self, token: LanguageToken, to_upos: ToUpos) -> None:
        pos = token.feature.pos1
        form = self._analysis_form(token.surface)
        lemmas = {parse.normal_form for parse in self._known(form) if to_upos(str(parse.tag))[0] == pos}
        token.feature.lemma = lemmas.pop() if len(lemmas) == 1 else form.lower()
