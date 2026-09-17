"""spaCy adapter shared by every spaCy language (spec §4.2).

``build_spacy_tagger`` is what a language's ``tokenizer.py::build_tagger``
returns; ``tagger_provider`` caches it (no edit there). The model is loaded
through its package's own ``load`` — ``importlib.import_module(package).load``
reads ``meta.json`` beside the package, needs no ``*.dist-info`` and so works
from a pack root, where ``spacy.load(name)`` (``is_package`` →
``importlib.metadata``) would not. ``spacy`` is imported only inside functions:
an install without the language's extra fails here, where ``tagger_provider``
turns the ImportError into its handled ValueError.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from anki_miner.languages._spaced.morphology import CAPITALISED_LEMMA_POS, EMPTY_MAP, tagging_copy
from anki_miner.languages._spaced.morphology import relemmatise_capitalised as _relemmatise_capitalised
from anki_miner.languages._spaced.tokens import to_duck_tokens
from anki_miner.languages.token import LanguageToken
from anki_miner.services.tagger import LockedTagger

#: Components never needed for mining: entities and the statistical sentence
#: segmenter. The parser is excluded too unless a language needs the arc.
_ALWAYS_EXCLUDED: tuple[str, ...] = ("ner", "senter")


def load_spacy_model(package: str, *, keep_parser: bool) -> Any:
    """Load ``package``'s pipeline minus ``ner``/``senter`` (and ``parser`` unless kept).

    The meta pipeline minus those names, never a fixed positive list (E.10
    D10: ro has no morphologizer, ca no tagger); spaCy ignores an excluded name
    a model does not have.
    """
    exclude = list(_ALWAYS_EXCLUDED) if keep_parser else [*_ALWAYS_EXCLUDED, "parser"]
    return importlib.import_module(package).load(exclude=exclude)


#: A pure, dictionary-free repair over one line's duck tokens (it ``prendere lo``). Runs inside every tagger call.
TokenPass = Callable[[list[LanguageToken]], list[LanguageToken]]


class SpacyTagger:
    """Callable with the fugashi tagger contract: ``tagger(text) -> list[LanguageToken]``."""

    def __init__(
        self,
        nlp: Any,
        *,
        title_case_pos: frozenset[str] = frozenset(),
        particle_deps: frozenset[str] = frozenset(),
        tag_char_map: Mapping[str, str] = EMPTY_MAP,
        post_passes: Sequence[TokenPass] = (),
        relemmatise_capitalised: bool = False,
        relemmatise_pos: frozenset[str] = CAPITALISED_LEMMA_POS,
    ) -> None:
        if any(len(key) != 1 or len(value) != 1 for key, value in tag_char_map.items()):
            raise ValueError("tag_char_map must map one character to one character")
        self.nlp = nlp
        self._title_case_pos = title_case_pos
        self._particle_deps = particle_deps
        self._tag_char_map = tag_char_map
        self._post_passes = tuple(post_passes)
        self._relemmatise_capitalised = relemmatise_capitalised
        self._relemmatise_pos = relemmatise_pos

    def __call__(self, text: str, **_: Any) -> list[LanguageToken]:
        """Tag the copy, build duck tokens over the original, then run the language's post-passes in order.

        Post-passes live here (spec §4.1/§4.2 ``post_passes``), not in the
        parser, because Card Backfill and the word filter call the tagger
        directly; a dictionary-gated repair stays the parser's ``token_post_pass``.
        ``relemmatise_capitalised`` repairs the doc's raw lemmas before the duck
        tokens (and so the casing rule) see them.
        """
        doc = self.nlp(tagging_copy(text, self._tag_char_map))
        if self._relemmatise_capitalised:
            _relemmatise_capitalised(doc, self._lemmatise_alone, pos=self._relemmatise_pos)
        tokens = to_duck_tokens(doc, text, title_case_pos=self._title_case_pos, particle_deps=self._particle_deps)
        for post_pass in self._post_passes:
            tokens = post_pass(tokens)
        return tokens

    def _lemmatise_alone(self, words: list[str]) -> list[str]:
        """Each word parsed on its own in one batch; ``""`` when the tokenizer splits a word."""
        return [doc[0].lemma_ if len(doc) == 1 else "" for doc in self.nlp.pipe(words)]

    def parse(self, text: str) -> list[LanguageToken]:
        """fugashi-compatible alias so ``LockedTagger.parse`` delegates cleanly."""
        return self(text)


#: A dash glued between two words (``know—really``, ``Wait--what``) is an interruption, never one word.
_DASH_INFIX = r"(?<=[{a}0-9])(?:–|—|--|---|——|~)(?=[{a}])"


def _splits_letter_hyphen_letter(pattern: str) -> bool:
    """Detected by behaviour, not by string: en, it and fr spell the rule differently."""
    match = re.search(pattern, "ab-cd")
    return match is not None and match.start() == 2


def _configure_infixes(nlp: Any, *, join_hyphenated: bool) -> None:
    """Rebuild the infix matcher: optionally without the hyphen rule, always with the dash rule (§4.2).

    ``join_hyphenated`` drops every default infix that splits two letters at an
    ASCII ``-`` (en ``well-known``, it ``italo-americano``, fr ``week-end`` stay whole; the ladder
    falls back to the parts) and raises when there is none to drop. The en rule
    also covered ``—``/``--``, and de es nl ca never split dashes at all, so the
    dash-only rule is appended for every spaCy language.
    """
    from spacy.lang.char_classes import ALPHA
    from spacy.util import compile_infix_regex

    infixes = list(nlp.Defaults.infixes)
    if join_hyphenated:
        kept = [pattern for pattern in infixes if not _splits_letter_hyphen_letter(pattern)]
        if len(kept) == len(infixes):
            raise ValueError("the model has no letter-hyphen-letter infix rule to remove")
        infixes = kept
    nlp.tokenizer.infix_finditer = compile_infix_regex([*infixes, _DASH_INFIX.format(a=ALPHA)]).finditer


_WORD_DOT = re.compile(r"[^\W\d_]+\.")


def _prune_dotted_rules(nlp: Any, abbreviations: frozenset[str]) -> None:
    """Drop tokenizer exceptions that glue an ordinary word to a sentence-final dot (NOTE 013).

    Every model's ``tokenizer.rules`` holds ``<stem>.`` keys whose stem is a
    common word (nl ``hand.``/``kon.``, de ``so.``, en ``Mass.``): the dot sticks
    to the word, the token becomes a dotted abbreviation (``X``) and the word
    silently vanishes at the end of a sentence. A key that is letters plus ONE
    final dot, whose casefolded stem is not in the language's S8 abbreviation
    set, is removed (all case variants); multi-dot keys (``z.B.``, ``e.g.``) stay.
    """
    nlp.tokenizer.rules = {
        key: value
        for key, value in nlp.tokenizer.rules.items()
        if not (_WORD_DOT.fullmatch(key) and key[:-1].casefold() not in abbreviations)
    }


def build_spacy_tagger(
    model: str,
    *,
    keep_parser: bool = False,
    title_case_pos: frozenset[str] = frozenset(),
    particle_deps: frozenset[str] = frozenset(),
    join_hyphenated: bool = False,
    tag_char_map: Mapping[str, str] = EMPTY_MAP,
    post_passes: Sequence[TokenPass] = (),
    abbreviations: frozenset[str] | None = None,
    relemmatise_capitalised: bool = False,
    relemmatise_pos: frozenset[str] = CAPITALISED_LEMMA_POS,
) -> LockedTagger:
    """Build the lock-guarded tagger for one spaCy model package.

    ``join_hyphenated`` is for a model whose defaults split productive
    letter-hyphen-letter compounds (en, it, fr); de es pt nl ca keep them whole
    already, and asking them to join raises. Dash-glued words split in every
    language (``_configure_infixes``). ``abbreviations`` is the language's S8
    set (the one it feeds ``sentence_rules``): one source of truth for "this
    dotted word is an abbreviation" — every other single-dot word exception is
    pruned (``_prune_dotted_rules``). ``relemmatise_capitalised`` opts into
    ``morphology.relemmatise_capitalised`` (sv: ``Huset`` → ``hus``) over
    ``relemmatise_pos`` (ro adds ``AUX``); a language that builds its own tagger
    over the returned ``.nlp`` (pt) must call that function itself.
    """
    if particle_deps and not keep_parser:
        raise ValueError("particle_deps need the dependency parser: pass keep_parser=True")
    nlp = load_spacy_model(model, keep_parser=keep_parser)
    _configure_infixes(nlp, join_hyphenated=join_hyphenated)
    if abbreviations is not None:
        _prune_dotted_rules(nlp, abbreviations)
    return LockedTagger(
        SpacyTagger(
            nlp,
            title_case_pos=title_case_pos,
            particle_deps=particle_deps,
            tag_char_map=tag_char_map,
            post_passes=post_passes,
            relemmatise_capitalised=relemmatise_capitalised,
            relemmatise_pos=relemmatise_pos,
        )
    )
