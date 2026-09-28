"""The pymorphy3 token post-pass shared by the Cyrillic spaCy languages (ru, uk).

``ru_core_news_sm`` and ``uk_core_news_sm`` both lemmatise through pymorphy3, and both need the same
repairs over the duck tokens: a joined hyphenated token the model never saw as one word; a verb the
model tagged as a noun or an adjective (sentence-initial imperatives: Подожди, Відчини); a content
token whose lemma is its own surface because no pymorphy3 parse matched the morphologizer's features
(``spacy/lang/ru/lemmatizer.py``: ``if not len(filtered_analyses): return [string.lower()]``); and an
adverb the lookup lemmatiser gave another word's normal form (``_pymorphy_lookup_lemmatize`` takes
the normal form of ANY parse, so uk можна fronted можний).
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
#: The model's tags for a verb it misreads (a capitalised imperative as a name, a noun, an adjective).
_VERB_MISTAGS = frozenset({"NOUN", "PROPN", "ADJ"})
#: ``oc2ud``'s VerbForm for OpenCorpora VERB and INFN; a gerund (Conv) or participle (Part) is not one.
_VERB_FORMS = frozenset({"Fin", "Inf"})

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
    2. A NOUN, PROPN or ADJ token whose every known parse is a finite verb or an infinitive
       becomes a VERB with the first parse's morph and the lemma branch 3 would give it: the model
       reads a capitalised dialogue-initial imperative as a name or a noun (Смотри PROPN, Подожди
       a masculine NOUN, uk Закрий ADJ), and the dictionary knows the word only as a verb. A
       gerund (дыша) or participle parse, or any non-verb parse, leaves the model's answer. It
       runs before the ``allowed_pos`` gate, which PROPN is outside.
    3. A content token whose lemma is its own surface under ``fold`` takes the one normal form that
       pymorphy3's known parses of the same UPOS agree on (вяжет -> вязать, сховалася ->
       сховатися), else the lower-cased ``analysis_form`` of its surface.
    4. An ADV token whose lemma is not its own surface, and whose every known parse is a form an
       adverb can share its spelling with (a vocative noun, a short neuter adjective, a singular
       full adjective in the nominative or vocative), takes the lower-cased ``analysis_form`` of
       its surface: pymorphy3-dicts-uk knows можна only as the adjective можний, уже and варто only
       as the vocatives of уж and варта, so the lookup lemmatiser fronted another word. A
       comparative keeps its adjective on purpose (громче -> громкий); so does a short adjective
       the model tagged ADV (глуп -> глупый), and a verb or a numeral (врёте -> врать) keeps the
       lemma its own parse accounts for.

    ``fold`` is what "the lemma is the surface" means for the language, and it exists because the
    tagging copy rewrites the surface before the model sees it: ``str.lower`` by default, yo-blind
    for ru (ё -> е), apostrophe-blind for uk. ``analysis_form`` is the spelling the analyser is
    asked for, in every branch, and the base of the surface fallbacks: identity by default,
    because ru's dictionaries know its surfaces as written, and the U+0027 canonicaliser for uk,
    whose dictionaries know only that one apostrophe -- ask them about a typographic ``м'яча`` and
    every parse comes back ``is_known=False``, so the repair would fall back onto the inflected
    form it exists to fix. ``allowed_pos`` is the language's ``allowed_pos`` gate.

    Not repaired: a gerund tagged NOUN (дыша), a participle tagged ADJ.
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
            pos = token.feature.pos1
            form = self._analysis_form(token.surface)
            hyphenated = _HYPHENATED.fullmatch(token.surface) is not None
            echoed = self._fold(token.feature.lemma) == self._fold(token.surface)
            identity = pos in self._allowed_pos and echoed
            # spaCy lemmatises a NOUN/PROPN/ADJ that no parse of its POS matches to its own text,
            # so a verb the model mistagged always echoes its surface.
            verb_mistag = pos in _VERB_MISTAGS and echoed
            other_adverb = (
                pos == "ADV" and token.feature.lemma != form.lower() and "Degree=Cmp" not in token.morph.split("|")
            )
            # The cheap checks above decide whether the token needs the analyser at all.
            if not (hyphenated or identity or verb_mistag or other_adverb):
                continue
            analyses = [
                (parse, *self._to_upos(str(parse.tag))) for parse in self._analyzer.parse(form) if parse.is_known
            ]
            if hyphenated:
                _retag(token, analyses)
            elif verb_mistag and _only_verbs(analyses):
                _retag(token, analyses)
                # The lemma the verb parses agree on, as for an identity lemma: Стой is the
                # imperative of стоять and of стоить, and the first parse would front the wrong one.
                token.feature.lemma = _agreed_lemma(analyses, "VERB", form)
            elif identity:
                token.feature.lemma = _agreed_lemma(analyses, pos, form)
            elif other_adverb and _spells_only_the_adverb(analyses):
                token.feature.lemma = form.lower()
        return tokens


#: A known parse with what ``to_upos`` makes of its tag: (parse, UPOS, UD features).
Analysis = tuple[Any, str, dict[str, str]]


def _retag(token: LanguageToken, analyses: list[Analysis]) -> None:
    """POS, lemma and morph from the first known parse; no known parse keeps the model's answer."""
    if not analyses:
        return
    parse, pos, features = analyses[0]
    token.feature.pos1 = pos
    token.feature.lemma = case_lemma(parse.normal_form, pos)
    token.morph = "|".join(f"{name}={value}" for name, value in sorted(features.items()))


def _agreed_lemma(analyses: list[Analysis], pos: str, form: str) -> str:
    """The one normal form the ``pos`` parses agree on, else the lower-cased ``form``."""
    lemmas = {parse.normal_form for parse, upos, _features in analyses if upos == pos}
    return lemmas.pop() if len(lemmas) == 1 else form.lower()


def _only_verbs(analyses: list[Analysis]) -> bool:
    """Some parse is known, and every one is a finite verb or an infinitive."""
    return bool(analyses) and all(
        pos == "VERB" and features.get("VerbForm") in _VERB_FORMS for _parse, pos, features in analyses
    )


def _spells_only_the_adverb(analyses: list[Analysis]) -> bool:
    """Some parse is known, and every one is a form an adverb shares its spelling with (можна, уже)."""
    return bool(analyses) and all(_adverb_homograph(pos, features) for _parse, pos, features in analyses)


def _adverb_homograph(pos: str, features: dict[str, str]) -> bool:
    """A vocative noun (uk уже: уж), a short neuter adjective (пытливо), or a singular full adjective
    in the nominative or vocative (uk можна: можний).

    A comparative, a plural, an oblique case or a masculine or feminine short form is the adjective
    itself, which the model tagged ADV: глуп, смешна, внутреннею and uk сиромудрі keep its lemma.
    """
    if pos == "NOUN":
        return features.get("Case") == "Voc"
    if pos != "ADJ" or features.get("Degree") == "Cmp" or features.get("Number") == "Plur":
        return False
    if features.get("Variant") == "Brev":
        return features.get("Gender") == "Neut"
    return features.get("Case") in ("Nom", "Voc")
