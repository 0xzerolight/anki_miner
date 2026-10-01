"""The Hebrew form resolver: a card front read out of the dictionary's own form table (spec F.2).

Hebrew has no tagger, so the front cannot come from morphology. It comes from ``wty-he-en``, which
keys 146,419 inflected forms as ``non-lemma`` rows whose glossary names the lemma they belong to.
The R36 seam (``SubtitleParserService(form_lookup=)``) is what lets this read them: the shipped
``AttestLookup`` answers "does this string exist" and cannot read a row's target.

**A form row's target is parsed out of the RENDERED content, not out of raw JSON**
(``is_lemma_row`` and ``form_targets``, shared with the spaCy languages' front repair in
``_spaced/form_of.py``). Reading the head line back out of rendered HTML is the shape
``fa/render.py`` already uses for its romanisation.

The resolution rules, in order, and what each is for (the first candidate with any row decides):

* **a lemma row whose every sense gloss is an inflection line fronts the lemma it names.** wty
  files many inflections under a lemma tag: ``ra'iti`` ``v sg suf`` is "First-person singular past
  (suffix conjugation) of ra'a", ``la'asot`` ``v`` is "to-infinitive of asa", ``ha-bayit``
  ``n def sg masc`` is "singular definite form of bayit". Only the ``glosses`` list is read, never
  the Etymology block (``oved``'s etymology is "Present participle of avad"; its sense is
  "worker"), and only when EVERY lemma row on the candidate reads that way -- one real sense
  (``nashim`` "women") keeps the candidate a headword. The entry names its own lemma the way a
  lemma row names itself, so no strip rung second-guesses it. Measured: 224 of 4,802 top-5,000
  forms (7.1 % of tokens) fronted such an inflection.
* a lemma row wins outright -- the word IS a headword -- and the front's part of speech and
  vocalisation come from its first row that is not a proper name (``yeter`` files the given name
  Jether before the noun "remainder", ``elohim`` the name before the noun).
* every lemma row a proper name: the name stands, unless the key also has form rows AND a strip
  rung confirms them (``ha-makom`` is a name for God; its form rows name ``makom``, which the strip
  ``makom`` confirms). With no strip to confirm, form rows never overturn a name: ``tsarfat``
  (France) would front ``tsiref`` and ``yeshu`` ``nasa``, so ``dvarim`` and ``esav`` stay names.
* form rows only: **never the surface**, whose only rows are bare lemma names and which therefore
  mines a card with no English (453 of 4,802 top-5,000 forms did). One distinct target is the
  front (``katavti`` -> ``katav``); several: the first in row order (``holekhet`` -> ``halakh``,
  ``lachzor`` -> ``chazor`` -> ``chazar``), right in 55 of a 64-form sample of the 421 ambiguous
  top-5,000 forms; its misses include ``yamim`` -> ``yam`` (sea, not day) and ``mimeni`` ->
  ``mimen``.
* a candidate -- the whole word, or a strip rung -- hit form rows only AND one of its own strip
  rungs is a headword: consult the strip before accepting a single target. Whole-word-first is a
  hazard, not a singleton -- 791 of the 2,966 proclitic-initial forms in the top 5,000 hit form
  rows only, and 295 of those have a strip rung on a lemma row of another key. A strip rung is
  held to the same check (``she-ha-kol``'s rung ``ha-kol`` is ``kol``, as ``ha-kol`` alone is).

  - If the strip's own resolutions intersect the target, the target stands (206 cases:
    ``ha-dvarim`` -> ``davar``) -- except behind the single article he, when the agreement came
    only through the strip's own form rows and the strip has a common lemma row: the strip is the
    front. ``chadash`` "new" is also a haser spelling of ``chodesh`` "month", so ``he-chadash`` was
    fronting "month" (modern "the month" is ``ha-chodesh``, keyed on its own); likewise
    ``ha-gadol``, ``ha-malon``, ``he-chashuv``. 15 top-5,000 forms change this way, one of them
    for the worse (``ha-elim`` "the gods" -> ``alim`` "violent"). A mem or shin strip keeps the
    target (``mevakesh`` -> ``bikesh``).
  - If they are disjoint (89 cases), the candidate's first letter decides: mem (the participle prefix)
    and shin front the target (``mevin`` -> ``hevin``), he/bet/kaf/lamed/vav front the strip
    (``ha-kol`` -> ``kol``, not ``hekhil``; ``ba-yom`` -> ``yom``; ``la-gan`` -> ``gan``).
    Measured over the 85 non-stopword disjoint forms: right 63 times, against 0 for the surface;
    the known misses are ``she-yesh``, ``she-hu``, ``she-kara`` (want the strip) and ``lekhi``,
    ``bata``, ``ba'ali``, ``hitslachta`` (want the target).

Every front the pass writes is then held to the function-word tier the tokenizer applies to a bare
surface, so ``she-lo`` and ``ve-gam`` drop the way ``lo`` and ``gam`` do.

``forms is None`` -- no offline dictionary wired, and every ja/ko/zh path -- makes the whole pass a
no-op, so the tokens reach the card exactly as the tokenizer built them.
"""

from __future__ import annotations

import html
import re
from typing import Any

from anki_miner.languages._spaced.form_of import form_targets, is_lemma_row, rendered_text
from anki_miner.languages.he.pos import pos_from_tags
from anki_miner.languages.he.proclitics import rungs
from anki_miner.languages.he.script import he_fold, is_he_letter, is_he_mark
from anki_miner.languages.he.stopwords import HE_FUNCTION_WORDS
from anki_miner.services.morphology import AttestLookup, FormLookup

__all__ = [
    "HebrewLemmaPass",
    "HebrewMinedForm",
    "HebrewReadingSupport",
    "he_audio_candidates",
    "he_speakable",
    "vocalised_from_content",
]

_GRAMMAR_HEAD_RE = re.compile(r'data-sc-content="Grammar-content"[^>]*>(.*?)</div>', re.S)
_BULLET = "\N{BULLET}"
#: How a Grammar head lists a lemma's spellings: ``akhshav / akhshav-pointed``,
#: ``likhtov \ likhtov``, ``chatsi or chetsi``, ``et, et-``.
_SPELLINGS_RE = re.compile(r" / | or | \\ |, ")
#: One line's worth of surfaces is small; the cache exists so a repeated word in a long corpus
#: (count_lemmas) is resolved once per parser, not once per occurrence.
_CACHE_MAX = 4096

#: ``FormLookup``'s answer: each key's ``(content, tags)`` rows, best entry first.
_Rows = dict[str, list[tuple[str, str]]]

#: Where a lemma row's sense list starts; the Grammar and Etymology blocks come before it.
_GLOSSES_MARK = 'data-sc-content="glosses">'
_GLOSS_ITEM = '<li class="gloss-sc-li">'
#: A sense's example sentences, and the part-of-speech chips some senses open with.
_DETAILS_RE = re.compile(r"<details.*?</details>", re.S)
_CHIPS_RE = re.compile(r'<div class="gloss-sc-div" data-sc-content="tags">.*?</div>', re.S)
#: The words an inflection line is made of: "Masculine singular present participle and present
#: tense of", "third-person masculine singular vav-consecutive imperfect (hence past tense) of",
#: "bare infinitive (infinitive construct or gerund) of", "plural indefinite form of". Measured over
#: every "... of <Hebrew>" gloss on the 15,308 lemma rows; a line with any other word names a
#: different word ("female equivalent of", "synonym of", "verbal noun of", "abbreviation of").
_INFLECTION_WORDS = frozenset(
    {
        "first-person",
        "second-person",
        "third-person",
        "masculine",
        "feminine",
        "singular",
        "plural",
        "dual",
        "definite",
        "indefinite",
        "construct",
        "state",
        "form",
        "present",
        "past",
        "future",
        "tense",
        "participle",
        "passive",
        "imperative",
        "infinitive",
        "to-infinitive",
        "bare",
        "absolute",
        "cohortative",
        "jussive",
        "vav-consecutive",
        "imperfect",
        "perfect",
        "suffix",
        "prefix",
        "conjugation",
        "hence",
        "gerund",
        "and",
        "or",
    }
)
#: A whole word opening with mem or shin is often a verb form in its own right -- mem is the
#: participle prefix (``mevin``, ``margish``), shin a root letter (``shamata``) -- so a disjoint
#: cross-check fronts its target; the other proclitic letters front the strip.
_STEM_INITIALS = frozenset("\N{HEBREW LETTER MEM}\N{HEBREW LETTER SHIN}")
_ARTICLE = "\N{HEBREW LETTER HE}"
#: A form row's deinflection pair keeps its rule chain on the target it names
#: (``yomitan_renderer.render_glossary_entry``): ``<span data-inflection="singular feminine
#: present">TARGET</span>``.
_PAIR_RE = re.compile(r'<span data-inflection="([^"]*)">(.*?)</span>', re.S)
#: The rules only a verb form carries: "third-person plural past", "singular feminine present",
#: "passive participle". A noun or adjective form reads "plural indefinite", "feminine".
_VERB_RULE_RE = re.compile(r"\b(?:past|future|present|imperative|infinitive|participle|passive|active)\b")
#: A tensed form outranks a possessed noun form of the same spelling -- halkhu 'they went', not
#: halakho 'his going' -- as modern speech says shel (spec section 9); an imperative does not
#: (darki 'my way', not dirkhi 'tread!').
_TENSE_RULE_RE = re.compile(r"\b(?:past|future)\b")
_POSSESSED = "possessed-form"


def vocalised_from_content(content: str) -> str:
    """The vocalised headword a lemma row's Grammar line opens with, or ``""``.

    ``kelev (bullet) (kelev, ...) m (plural ...)`` -- everything before the bullet. Present on
    15,243 of the 15,308 lemma rows of revision 2026.09.19. A head that lists several spellings
    (``akhshav / akhshav-pointed``: 337 of 1,927 mined top-5,000 fronts) gives its first pointed
    one, else its first: the reading is ONE word, and the voice speaks it once.
    """
    match = _GRAMMAR_HEAD_RE.search(content or "")
    if match is None:
        return ""
    head = rendered_text(match.group(1))
    bullet = head.find(_BULLET)
    if bullet <= 0:
        return ""
    spellings = [spelling.strip() for spelling in _SPELLINGS_RE.split(head[:bullet]) if spelling.strip()]
    pointed = [spelling for spelling in spellings if any(is_he_mark(char) for char in spelling)]
    return (pointed or spellings or [""])[0]


def _sense_glosses(content: str) -> list[str]:
    """The text of each sense in a lemma row's ``glosses`` list, examples and chips dropped."""
    start = content.find(_GLOSSES_MARK)
    if start < 0:
        return []
    senses: list[str] = []
    for item in content[start:].split(_GLOSS_ITEM)[1:]:
        text = rendered_text(_CHIPS_RE.sub("", _DETAILS_RE.sub("", item.split("</li>", 1)[0])))
        if text:
            senses.append(text)
    return senses


def _inflection_of(gloss: str) -> str:
    """The folded lemma an inflection line names ("to-infinitive of asa (asa)."), else ``""``."""
    grammar, of, rest = gloss.partition(" of ")
    words = grammar.lower().replace("(", " ").replace(")", " ").replace(",", " ").split()
    if not of or not words or not all(word in _INFLECTION_WORDS for word in words):
        return ""
    named = he_fold(rest.split(maxsplit=1)[0].rstrip(".,:;")) if rest.strip() else ""
    return named if named and is_he_letter(named[0]) else ""


def _lemma_of(heads: list[tuple[str, str]]) -> list[str]:
    """The lemmas a candidate's lemma rows name when EVERY one is an inflection line, else ``[]``."""
    named: list[str] = []
    for content, _tags in heads:
        lemmas = [_inflection_of(gloss) for gloss in _sense_glosses(content)]
        if not lemmas or not all(lemmas):
            return []
        for lemma in lemmas:
            if lemma not in named:
                named.append(lemma)
    return named


def _form_row_targets(found: list[tuple[str, str]]) -> list[str]:
    """The folded lemmas a key's form rows name, first seen first."""
    targets: list[str] = []
    for content, tags in found:
        if is_lemma_row(tags):
            continue
        for target in form_targets(content):
            folded = he_fold(target)
            if folded not in targets:
                targets.append(folded)
    return targets


def _verb_form_of(found: list[tuple[str, str]], target: str) -> bool:
    """Whether a key's form rows name *target* as a verb form, by the pairs' own rules.

    Every pair naming it a verb form, or a tensed one against possessed forms only. A spelling
    the dictionary also files as a noun or adjective form of the target (banim 'sons', chayevet
    'must') is no verb form, and neither is a row imported before the renderer kept the rules.
    Measured over the top 5,000: 27 forms carry both kinds of pair. Any verb pair would make all
    27 verbs, 13 of them wrongly (129k tokens, chayevet alone 60k); this rule is wrong on 3.
    """
    rules = [
        html.unescape(chain)
        for content, tags in found
        if not is_lemma_row(tags)
        for chain, term in _PAIR_RE.findall(content)
        if he_fold(rendered_text(term)) == target
    ]
    if not rules:
        return False
    if all(_VERB_RULE_RE.search(chain) for chain in rules):
        return True
    return any(_TENSE_RULE_RE.search(chain) for chain in rules) and all(
        _VERB_RULE_RE.search(chain) or _POSSESSED in chain for chain in rules
    )


def _is_name(tags: str) -> bool:
    return pos_from_tags(tags) == "PROPN"


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
        if len(self._cache) >= _CACHE_MAX:
            # Clear between lines, never inside one: the apply loop below reads every
            # WORD token of this line back from the cache.
            self._cache.clear()
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
            if resolved.lemma in HE_FUNCTION_WORDS:
                # The tokenizer tests its tier on the folded SURFACE; a proclitic form of a function
                # word (she-lo, ve-az, ba-kol) reaches the same folded front here and drops the
                # same way, whatever part of speech wty gives the bare word (lo 'adv', kol 'n').
                token.feature.pos2 = "stopword"
        return tokens

    @staticmethod
    def _candidates(key: str) -> list[str]:
        return [key, *rungs(key)]

    @staticmethod
    def _read(forms: FormLookup, wanted: list[str]) -> _Rows:
        """One batched read; a dictionary failure is a miss, never an exception out of a parse."""
        if not wanted:
            return {}
        try:
            return forms(wanted)
        except Exception:  # noqa: BLE001 - a dictionary failure must never break a parse
            return {}

    def _remember(self, key: str, rows: _Rows) -> None:
        self._cache[key] = self._resolve(key, rows)

    def _resolve(self, key: str, rows: _Rows) -> _Resolution:
        """The module docstring's rules, in order, over the key's ladder."""
        for candidate in self._candidates(key):
            found = rows.get(candidate) or []
            if not found:
                continue
            resolved = self._headword(candidate, rows)
            if resolved is not None:
                return resolved
            names_only = any(is_lemma_row(tags) for _content, tags in found)
            targets = _form_row_targets(found)
            # The candidate's OWN strips, at a strip rung as at the whole word: she-ha-kol's rung
            # ha-kol is checked against kol exactly as ha-kol alone is.
            strip = self._strip_head(rungs(candidate), rows) if targets else None
            agrees = strip is not None and not self._own(strip, rows).isdisjoint(targets)
            if names_only and not agrees:
                # Every lemma row a proper name, and no strip confirms its form rows: the name stands.
                return self._front(candidate, rows)
            if not targets:
                # Form rows that name nothing the renderer can read: nothing better to front.
                return _Resolution(key, "WORD", "", "")
            if strip is not None and len(targets) == 1:
                if agrees and strip not in targets and candidate == _ARTICLE + strip:
                    # Agreement only through the strip's own (haser) form rows, behind the
                    # article: the strip's own headword (he-chadash is chadash, not chodesh).
                    resolved = self._headword(strip, rows)
                    if resolved is not None:
                        return resolved
                elif not agrees and candidate[0] not in _STEM_INITIALS:
                    # Disjoint behind he/bet/kaf/lamed/vav: the strip (ha-kol is kol, not hekhil).
                    return self._headword(strip, rows) or self._front(strip, rows)
            return self._front(targets[0], rows, _verb_form_of(found, targets[0]))
        return _Resolution(key, "WORD", "", "")

    def _headword(self, key: str, rows: _Rows) -> _Resolution | None:
        """The key's answer from its own lemma rows; ``None`` when it has none, or only proper names.

        Every lemma row an inflection line: the lemma the first one names, through its verb row when
        the entry is filed as a verb (holekh 'v masc ptcpl sg' is halakh, not helekh). Otherwise the
        key itself.
        """
        heads = [(content, tags) for content, tags in rows.get(key) or [] if is_lemma_row(tags)]
        named = _lemma_of(heads)
        if named:
            return self._front(named[0], rows, all(pos_from_tags(tags) == "VERB" for _content, tags in heads))
        if any(not _is_name(tags) for _content, tags in heads):
            return self._front(key, rows)
        return None

    @staticmethod
    def _strip_head(strips: list[str], rows: _Rows) -> str | None:
        """The first strip rung that is a headword -- any lemma row -- or ``None``."""
        for strip in strips:
            if any(is_lemma_row(tags) for _content, tags in rows.get(strip) or []):
                return strip
        return None

    @staticmethod
    def _own(strip: str, rows: _Rows) -> set[str]:
        """What a strip rung resolves to on its own: itself, plus every lemma its form rows name."""
        return {strip, *_form_row_targets(rows.get(strip) or [])}

    @staticmethod
    def _front(lemma: str, rows: _Rows, verb_form: bool = False) -> _Resolution:
        """*lemma* as the front, its pos1 and vocalisation from its first lemma row that is not a
        proper name (the first lemma row when every one is), when the batch has it.

        ``verb_form``: the form that named *lemma* is a verb inflection, so its first ``v`` row is
        the one (halkhu is halakh 'went', never helekh 'traveler', wty's first row).
        """
        heads = [(content, tags) for content, tags in rows.get(lemma) or [] if is_lemma_row(tags)]
        chosen = [row for row in heads if not _is_name(row[1])] or heads
        if verb_form:
            chosen = [row for row in chosen if pos_from_tags(row[1]) == "VERB"] or chosen
        if not chosen:
            return _Resolution(lemma, "WORD", "", "")
        content, tags = chosen[0]
        return _Resolution(lemma, pos_from_tags(tags), vocalised_from_content(content), tags)


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
