"""The Hebrew form resolver: a card front read out of the dictionary's own form table (spec F.2).

Hebrew has no tagger, so the front cannot come from morphology. It comes from ``wty-he-en``, which
keys 146,419 inflected forms as ``non-lemma`` rows whose glossary names the lemma they belong to.
The R36 seam (``SubtitleParserService(form_lookup=)``) is what lets this read them: the shipped
``AttestLookup`` answers "does this string exist" and cannot read a row's target.

**A form row's target is parsed out of the RENDERED content, not out of raw JSON.** The Yomitan
importer stores ``render_glossary_entry(...)`` output in the ``content`` column
(``yomitan_importer.py``), so a single-target row arrives as
``<li class="gloss-item"><div class="gloss-content">LEMMA</div></li>`` and a multi-target row wraps
its targets in ``<li class="gloss-sc-li">``. Reading the head line back out of rendered HTML is the
shape ``fa/render.py`` already uses for its romanisation.

The resolution rules, in order, and what each is for:

* a lemma row on the candidate wins outright -- the word IS a headword (``ha-bayit`` is its own
  ``n def sg masc`` row, so it never becomes ``bayit``);
* form rows only, one distinct target: that target is the front (``katavti`` -> ``katav``);
* form rows only, more than one target: **the surface stays**. The candidate at that point hit form
  rows only, so it is not a headword either, and fronting it would print a string the dictionary
  cannot define -- measured over the 5,000 commonest forms, 45 stripped rungs are ambiguous this
  way (``lachzor`` -> ``chazor`` -> {``chazar``, ``chizer``});
* the whole word hit form rows only AND a strip rung is a real headword: consult the strip before
  accepting the target. Whole-word-first is a hazard, not a singleton -- 791 of the 2,966
  proclitic-initial forms in that same sample hit form rows only, and 295 of those have a strip rung
  on a lemma row of another key. If the strip's own resolutions intersect the whole word's targets
  the target stands (206 cases: ``ha-dvarim`` -> ``davar``); if they are disjoint the surface stays
  (89 cases: ``ha-kol`` does not become ``hekhil``, ``ba-yom`` not ``biyem``, ``la-gan`` not ``log``).

``forms is None`` -- no offline dictionary wired, and every ja/ko/zh path -- makes the whole pass a
no-op, so the tokens reach the card exactly as the tokenizer built them.
"""

from __future__ import annotations

import re
from typing import Any

from anki_miner.languages.he.pos import pos_from_tags
from anki_miner.languages.he.proclitics import rungs
from anki_miner.languages.he.script import he_fold
from anki_miner.services.morphology import AttestLookup, FormLookup

__all__ = [
    "HebrewLemmaPass",
    "HebrewMinedForm",
    "HebrewReadingSupport",
    "form_targets",
    "he_audio_candidates",
    "he_speakable",
    "is_lemma_row",
    "vocalised_from_content",
]

_GLOSS_CONTENT_RE = re.compile(r'<div class="gloss-content">(.*?)</div>', re.S)
_GLOSS_ITEM_RE = re.compile(r'<li class="gloss-sc-li">(.*?)</li>', re.S)
_GRAMMAR_HEAD_RE = re.compile(r'data-sc-content="Grammar-content"[^>]*>(.*?)</div>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_BULLET = "\N{BULLET}"
_NON_LEMMA = "non-lemma"
#: One line's worth of surfaces is small; the cache exists so a repeated word in a long corpus
#: (count_lemmas, the Deck Builder preview) is resolved once per parser, not once per occurrence.
_CACHE_MAX = 4096


def _text(html: str) -> str:
    import html as html_lib

    return html_lib.unescape(_TAG_RE.sub("", html)).strip()


def is_lemma_row(tags: str) -> bool:
    """A row the dictionary files as a headword rather than as an inflected form."""
    return _NON_LEMMA not in tags.split(" ")


def form_targets(content: str) -> list[str]:
    """The lemmas a form row's rendered content names, in order (spec F.2, measured shapes)."""
    found: list[str] = []
    for block in _GLOSS_CONTENT_RE.findall(content or ""):
        items = _GLOSS_ITEM_RE.findall(block)
        for item in items or [block]:
            target = _text(item)
            if target:
                found.append(target)
    return found


def vocalised_from_content(content: str) -> str:
    """The vocalised headword a lemma row's Grammar line opens with, or ``""``.

    ``kelev (bullet) (kelev, ...) m (plural ...)`` -- everything before the bullet. Present on
    15,243 of the 15,308 lemma rows of revision 2026.09.19.
    """
    match = _GRAMMAR_HEAD_RE.search(content or "")
    if match is None:
        return ""
    head = _text(match.group(1))
    bullet = head.find(_BULLET)
    return head[:bullet].strip() if bullet > 0 else ""


class _Resolution:
    """What the dictionary says about one folded surface."""

    __slots__ = ("lemma", "pos1", "vocalised", "tags")

    def __init__(self, lemma: str, pos1: str, vocalised: str, tags: str) -> None:
        self.lemma = lemma
        self.pos1 = pos1
        self.vocalised = vocalised
        self.tags = tags


class HebrewLemmaPass:
    """``token_post_pass``: resolve every ``WORD`` token's front against the dictionary.

    One batched read per line over every candidate of every token, then a per-surface cache. Never
    raises: a lookup that fails leaves the line exactly as the tokenizer built it, which is the
    same output a Hebrew install with no dictionary produces.
    """

    def __init__(self) -> None:
        self._cache: dict[str, _Resolution] = {}

    def __call__(self, tokens: list[Any], attest: AttestLookup | None, forms: FormLookup | None) -> list[Any]:
        del attest  # existence is not enough: this pass needs the rows themselves
        if forms is None:
            return tokens
        pending = [t for t in tokens if t.feature.pos1 == "WORD" and t.feature.lemma not in self._cache]
        if pending:
            wanted: list[str] = []
            for token in pending:
                for candidate in self._candidates(token.feature.lemma):
                    if candidate not in wanted:
                        wanted.append(candidate)
            rows = self._read(forms, wanted)
            # A resolved target is a key of its own, and nothing asked for it in the first batch:
            # the front comes from the form row, but its part of speech and its vocalisation live
            # on the TARGET's lemma row. One more batched read fills them (spec F.2).
            resolved_keys = {self._resolve(t.feature.lemma, rows).lemma for t in pending}
            follow_up = [key for key in resolved_keys if key not in rows]
            if follow_up:
                rows = {**rows, **self._read(forms, follow_up)}
            for token in pending:
                self._remember(token.feature.lemma, rows)
        for token in tokens:
            if token.feature.pos1 != "WORD":
                continue
            resolved = self._cache.get(token.feature.lemma)
            if resolved is None:
                continue
            token.feature.lemma = resolved.lemma
            token.feature.pos1 = resolved.pos1
            token.feature.vocalised = resolved.vocalised
            token.feature.dict_tags = resolved.tags
        return tokens

    @staticmethod
    def _candidates(key: str) -> list[str]:
        return [key, *rungs(key)]

    @staticmethod
    def _read(forms: FormLookup, wanted: list[str]) -> dict[str, list[tuple[str, str]]]:
        """One batched read; a dictionary failure is a miss, never an exception out of a parse."""
        if not wanted:
            return {}
        try:
            return forms(wanted)
        except Exception:  # noqa: BLE001 - a dictionary failure must never break a parse
            return {}

    def _remember(self, key: str, rows: dict[str, list[tuple[str, str]]]) -> None:
        if len(self._cache) >= _CACHE_MAX:
            self._cache.clear()
        self._cache[key] = self._resolve(key, rows)

    def _resolve(self, key: str, rows: dict[str, list[tuple[str, str]]]) -> _Resolution:
        candidates = self._candidates(key)
        for index, candidate in enumerate(candidates):
            found = rows.get(candidate) or []
            if not found:
                continue
            lemma_rows = [(content, tags) for content, tags in found if is_lemma_row(tags)]
            if lemma_rows:
                content, tags = lemma_rows[0]
                return _Resolution(candidate, pos_from_tags(tags), vocalised_from_content(content), tags)
            targets = {he_fold(target) for content, _tags in found for target in form_targets(content)}
            if len(targets) != 1:
                # More than one lemma, or none the renderer named: the surface is the honest
                # answer, at rung 0 and at a stripped rung alike.
                return _Resolution(key, "WORD", "", "")
            target = next(iter(targets))
            if index == 0 and not self._strip_agrees(candidates[1:], rows, targets):
                return _Resolution(key, "WORD", "", "")
            return self._from_target(target, rows)
        return _Resolution(key, "WORD", "", "")

    @staticmethod
    def _strip_agrees(strips: list[str], rows: dict[str, list[tuple[str, str]]], targets: set[str]) -> bool:
        """The cross-check: does the first strip rung that is a headword agree with the target?

        No strip rung is a headword ⇒ nothing contradicts the target and it stands.
        """
        for strip in strips:
            found = rows.get(strip) or []
            if not found or not any(is_lemma_row(tags) for _content, tags in found):
                continue
            own = {strip}
            own.update(
                he_fold(target) for content, tags in found if not is_lemma_row(tags) for target in form_targets(content)
            )
            return bool(own & targets)
        return True

    @staticmethod
    def _from_target(target: str, rows: dict[str, list[tuple[str, str]]]) -> _Resolution:
        """Fill pos1 and the vocalisation from the target's own lemma row, when the batch has it."""
        for content, tags in rows.get(target) or []:
            if is_lemma_row(tags):
                return _Resolution(target, pos_from_tags(tags), vocalised_from_content(content), tags)
        return _Resolution(target, "WORD", "", "")


class HebrewMinedForm:
    """The resolver's output is the card front, for every POS.

    ``lemma`` already IS the surface whenever nothing resolved, so there is no per-POS rule to
    write: a resolved word fronts its dictionary form and an unresolved one fronts what was said.
    """

    def mined_form(
        self, pos: str | None, orth_base: str, lemma: str, surface: str, pronunciation: str | None = None
    ) -> str:
        del pos, orth_base, pronunciation
        return lemma or surface

    def expression_tracks_surface(self, word: Any) -> bool:
        """A lemma front never follows an i+1 swap's new surface (S12)."""
        del word
        return False


class HebrewReadingSupport:
    """``expression_reading`` = the vocalised headword the resolver stored on the token.

    Blank for an unresolved word and for the 65 lemma rows that carry no Grammar line: a Hebrew
    reading is something the dictionary knows, never something spelling can derive.
    """

    def word_reading(self, token: Any) -> str:
        return str(getattr(token.feature, "vocalised", "") or "")


def he_audio_candidates(word: Any) -> list[tuple[str, str]]:
    """Word-audio ladder: ``(front, vocalised)`` first -- gTTS ``iw`` pronounces the points."""
    term = str(getattr(word, "mined_form", "") or "")
    if not term:
        return []
    reading = str(getattr(word, "expression_reading", "") or "")
    return [(term, reading), (term, term)] if reading and reading != term else [(term, term)]


def he_speakable(term: str, reading: str) -> str | None:
    """What a Hebrew voice may speak: the vocalised reading when there is one, else the front."""
    return reading or term or None
