"""Card fronts repaired from the dictionary's own form-of rows (spec R36 ``form_lookup``).

A Wiktionary-derived dictionary (``wty-*``) keys every inflected form it knows as a ``non-lemma``
row whose glossary names the lemma it belongs to. ``service_factory`` wires those rows into every
parser as R36's ``form_lookup`` (``DefinitionService.offline_term_rows``), and a ``token_post_pass``
is their only reader. Hebrew's ``HebrewLemmaPass`` reads them to build a front the tokenizer
cannot; ``FormOfLemmaPass`` reads them to repair a front the tagger got wrong (sv ``fönstret``
lemmatised ``fönstr``, pl ``Zapomniałeś`` lemmatised ``zapomniać``, de ``Äpfel`` left unlemmatised).

**A form row's target is parsed out of the RENDERED content, not out of raw JSON.** The Yomitan
importer stores ``render_glossary_entry(...)`` output in the ``content`` column
(``yomitan_importer.py``), so a single-target row arrives as
``<li class="gloss-item"><div class="gloss-content">LEMMA</div></li>`` and a multi-target row wraps
its targets in ``<li class="gloss-sc-li">``.

The pass touches only an ADJ/ADV/NOUN/VERB token whose lemma has no lemma row: a lemma the
dictionary files as a headword is never second-guessed, whatever its part of speech (de
``Hochdeutsch`` stays, although ``hochdeutsch`` also has a form row naming ``Hochdeutsche``), and
neither is a separable-verb head the tagger already joined (``_tagger_joined``). A language may add
the classes its tagger puts content words in by mistake (``recover_pos``, el ``X``/``PROPN``). Its
candidates are read in order, and the first one the dictionary has any row for decides:

* the lowercased SURFACE first -- it is what the text holds, where the lemma is the tagger's guess
  (sv ``Mötet``: the surface's row names the noun ``möte``, the lemma ``möt``'s the verb ``möta``);
* a candidate with a lemma row is itself the front (sv ``sänka`` for the lemma ``sänk``);
* a candidate with form rows only names exactly one target, and that target has a lemma row: the
  target is the front. Several targets, or one the dictionary does not file as a headword, and the
  token stays as the tagger built it: pl ``mili`` names ``miły`` and ``mila``, and falling through
  to its lemma ``mić`` would front ``nić`` (thread); pl ``mailem`` names ``mail``, itself a form row;
* then the tagger's lemma, through its form rows the same way.

The new front's ``pos1`` is the part of speech its first lemma row opens with (de ``Hör``, tagged a
noun, becomes the verb ``hören``), and the front is cased the way the tokenizer cases every lemma:
``case_lemma`` with the language's ``title_case_pos`` (de ``Äpfel`` -> ``Apfel``).

``forms is None`` -- no offline dictionary wired, and every fixture parser -- makes the pass a
no-op, so the tokens reach the card exactly as the tokenizer built them.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from anki_miner.languages._spaced.morphology import case_lemma
from anki_miner.languages._spaced.pos import UPOS_ALLOWED

if TYPE_CHECKING:  # annotation-only: services must not load at profile build
    from anki_miner.services.morphology import AttestLookup, FormLookup, TokenPostPass

__all__ = [
    "WTY_TAG_TO_UPOS",
    "FormOfLemmaPass",
    "OrderedPasses",
    "form_targets",
    "is_lemma_row",
    "lemma_row_targets",
    "rendered_text",
]

_GLOSS_CONTENT_RE = re.compile(r'<div class="gloss-content">(.*?)</div>', re.S)
_GLOSS_ITEM_RE = re.compile(r'<li class="gloss-sc-li">(.*?)</li>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_NON_LEMMA = "non-lemma"
#: One line's worth of keys is small; the cache exists so a repeated word in a long corpus
#: (count_lemmas) is read once per parser, not once per occurrence.
_CACHE_MAX = 4096

#: One ``(content, tags)`` row as ``FormLookup`` returns it.
Row = tuple[str, str]


def rendered_text(markup: str) -> str:
    """The text of a piece of rendered glossary HTML: tags dropped, entities decoded, trimmed."""
    return html.unescape(_TAG_RE.sub("", markup)).strip()


def is_lemma_row(tags: str) -> bool:
    """A row the dictionary files as a headword rather than as an inflected form."""
    return _NON_LEMMA not in tags.split(" ")


def form_targets(content: str) -> list[str]:
    """The lemmas a form row's rendered content names, in order (spec F.2, measured shapes)."""
    found: list[str] = []
    for block in _GLOSS_CONTENT_RE.findall(content or ""):
        items = _GLOSS_ITEM_RE.findall(block)
        for item in items or [block]:
            target = rendered_text(item)
            if target:
                found.append(target)
    return found


def lemma_row_targets(content: str, tags: str) -> list[str] | None:
    """The default row reading: ``None`` for a lemma row, else the lemmas the form row names."""
    return None if is_lemma_row(tags) else form_targets(content)


#: The part of speech a wty lemma row's definition tags open with, as UPOS. Over the wty de nl sv
#: nb da lt dictionaries ``n adj v name adv`` open 97-99 % of lemma rows and the closed classes
#: most of the rest; any other first tag (``phrase``, ``pref``, ``suf``, ``prov``, ``char``, none)
#: names no class, and the token keeps its own.
WTY_TAG_TO_UPOS: Mapping[str, str] = MappingProxyType(
    {
        "n": "NOUN",
        "v": "VERB",
        "adj": "ADJ",
        "adv": "ADV",
        "name": "PROPN",
        "pron": "PRON",
        "det": "DET",
        "artic": "DET",
        "prep": "ADP",
        "postp": "ADP",
        "conj": "CCONJ",
        "num": "NUM",
        "intj": "INTJ",
        "ptcl": "PART",
    }
)

#: How a row is read: ``None`` for a lemma row, else the targets the form row names.
RowTargets = Callable[[str, str], list[str] | None]
#: ``(token, front, the front's lemma rows) -> bool``: whether the pass may adopt that front.
FrontPredicate = Callable[[Any, str, Sequence[Row]], bool]
#: More spellings of a token's surface, tried right after the surface itself.
ExtraCandidates = Callable[[Any], Iterable[str]]


def _tagger_joined(token: Any) -> bool:
    """A verb head whose lemma already starts with its stashed separable particle: the tagger joined it.

    nl ``staat … bekend`` is lemmatised ``bekendstaan``, which wty-nl-en lacks. Its surface would
    read as the noun ``staat`` (state), or through its form row as the bare ``staan``; the join
    (``SeparableVerbPass``) decides that head instead.
    """
    particle = getattr(token.feature, "particle", "")
    return bool(particle) and token.feature.lemma.startswith(particle)


class FormOfLemmaPass:
    """``token_post_pass``: repair a content token's lemma from the dictionary's form-of rows.

    The rule is the module docstring's. Every knob defaults to that plain rule; a language sets one
    from its own ``parser.py``:

    * ``title_case_pos`` -- the classes the language's tokenizer title-cases (de nouns).
    * ``surface_first`` -- ``False`` reads the tagger's lemma before the surface.
    * ``accept(token, front, lemma_rows)`` -- a gate every front must pass.
    * ``same_pos`` -- only a lemma row of the token's own UPOS may make the front.
    * ``extra_candidates(token)`` -- more surface spellings, tried right after the surface.
    * ``row_targets(content, tags)`` -- how a row is read: ``None`` for a lemma row, else the
      targets it names (a dictionary whose form rows are tagged as lemmas, or whose targets carry
      marks its keys fold away).
    * ``recover_pos`` -- classes the tagger puts content words in by mistake (el ``X``/``PROPN`` for
      a cue-initial verb). Such a token is read like a content token but never kept on its own
      lemma, since its class is the mistake; it changes only into a content word, taking the
      front's class, and ``accept`` decides which fronts it may take.

    One batched read per line for the candidates and one for the targets they name, both through a
    per-key cache. The surface is lowered with ``str.lower()``, never ``casefold``, which would
    rewrite de ``ß`` and el final sigma on the card.
    """

    def __init__(
        self,
        *,
        title_case_pos: frozenset[str] = frozenset(),
        surface_first: bool = True,
        accept: FrontPredicate | None = None,
        same_pos: bool = False,
        extra_candidates: ExtraCandidates | None = None,
        row_targets: RowTargets = lemma_row_targets,
        recover_pos: frozenset[str] = frozenset(),
    ) -> None:
        self._title_case_pos = title_case_pos
        self._surface_first = surface_first
        self._accept = accept
        self._same_pos = same_pos
        self._extra_candidates = extra_candidates
        self._row_targets = row_targets
        self._recover_pos = recover_pos
        self._read_pos = frozenset(UPOS_ALLOWED) | recover_pos
        self._cache: dict[str, list[Row]] = {}

    def __call__(self, tokens: list[Any], attest: AttestLookup | None, forms: FormLookup | None) -> list[Any]:
        del attest  # existence is not enough: this pass needs the rows themselves
        if forms is None:
            return tokens
        content = [token for token in tokens if token.feature.pos1 in self._read_pos and not _tagger_joined(token)]
        if not content:
            return tokens
        rows = self._read(forms, [key for token in content for key in self._candidates(token)])
        choices = [(token, self._choice(token, rows)) for token in content]
        # A target is a key of its own that the first batch did not ask for: its lemma rows, which
        # carry the front's part of speech, need one more batched read.
        rows.update(self._read(forms, [choice for _token, choice in choices if choice and choice not in rows]))
        for token, choice in choices:
            front = self._adopt(token, choice, rows) if choice else None
            if front is not None:
                token.feature.lemma, token.feature.pos1 = front
        return tokens

    def _read(self, forms: FormLookup, keys: list[str]) -> dict[str, list[Row]]:
        """Every key's rows (``[]`` for none): the cached ones, and one batched read for the rest."""
        keys = [key for key in dict.fromkeys(keys) if key]
        found = {key: self._cache[key] for key in keys if key in self._cache}
        wanted = [key for key in keys if key not in found]
        if wanted:
            read = forms(wanted)
            if len(self._cache) + len(wanted) > _CACHE_MAX:
                self._cache.clear()
            for key in wanted:
                found[key] = self._cache[key] = read.get(key, [])
        return found

    def _candidates(self, token: Any) -> list[str]:
        """The keys a token may resolve through, in reading order; the lemma is always among them."""
        surface = [token.surface.lower()]
        if self._extra_candidates is not None:
            surface.extend(self._extra_candidates(token))
        lemma = [token.feature.lemma]
        return list(dict.fromkeys(surface + lemma if self._surface_first else lemma + surface))

    def _heads(self, rows: Iterable[Row]) -> list[Row]:
        return [(content, tags) for content, tags in rows if self._row_targets(content, tags) is None]

    def _choice(self, token: Any, rows: Mapping[str, list[Row]]) -> str | None:
        """The front the dictionary points at, before its own lemma rows are checked; ``None`` keeps the token."""
        if token.feature.pos1 not in self._recover_pos and self._heads(rows.get(token.feature.lemma, ())):
            return None
        for candidate in self._candidates(token):
            found = rows.get(candidate)
            if not found:
                continue
            if self._heads(found):
                return candidate
            targets = {target: None for content, tags in found for target in self._row_targets(content, tags) or ()}
            return next(iter(targets)) if len(targets) == 1 else None
        return None

    def _adopt(self, token: Any, front: str, rows: Mapping[str, list[Row]]) -> tuple[str, str] | None:
        """``(lemma, pos1)`` from the front's first lemma row, or ``None`` when no row or gate allows it."""
        heads = self._heads(rows.get(front, ()))
        if self._same_pos:
            heads = [row for row in heads if WTY_TAG_TO_UPOS.get(row[1].split(" ")[0]) == token.feature.pos1]
        if not heads or (self._accept is not None and not self._accept(token, front, heads)):
            return None
        pos1 = WTY_TAG_TO_UPOS.get(heads[0][1].split(" ")[0], token.feature.pos1)
        if token.feature.pos1 in self._recover_pos and pos1 not in UPOS_ALLOWED:
            return None
        return case_lemma(front, pos1, self._title_case_pos), pos1


class OrderedPasses:
    """One ``token_post_pass`` running several in order: the parser seam takes a single callable."""

    def __init__(self, *passes: TokenPostPass) -> None:
        self._passes = passes

    def __call__(self, tokens: list[Any], attest: AttestLookup | None, forms: FormLookup | None) -> list[Any]:
        for post_pass in self._passes:
            tokens = post_pass(tokens, attest, forms)
        return tokens
