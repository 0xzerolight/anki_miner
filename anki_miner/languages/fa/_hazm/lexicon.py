"""Every Persian table the tokenizer asks, built once from the pack and the TSVs.

The formal verb table is the 79 patterns expanded over ``verbs.dat``; the
informal one is the 30 present patterns expanded over ``iverbs.dat``'s informal
stems, paired form for form with the formal spelling they stand for. hazm's own
``informal_to_formal_conjucation`` is NOT ported: it zips two conjugation lists
at hard-coded offsets and is wrong upstream (probe P-5 -- it maps miram to a
three-word string meaning "they had been going").

Built once per process and never evicted: ``languages/tagger_provider.py`` says
so in its first line, and nothing on main evicts. The honest cost is about 14 MB
resident from the first Persian parse until the process exits.
"""

from __future__ import annotations

import csv
from importlib.resources import files
from typing import TYPE_CHECKING

from anki_miner.languages.fa import script as fa_script
from anki_miner.languages.fa._hazm import conjugation
from anki_miner.languages.fa._hazm.data import HazmData

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

#: Where the two committed tables live (package data, both build paths).
DATA_PACKAGE = "anki_miner.languages.fa.data"
COMPOUND_VERBS_FILE = "compound_verbs.tsv"
COLLOQUIAL_FILE = "colloquial.tsv"

ZWNJ = "\N{ZERO WIDTH NON-JOINER}"


def _read_tsv(name: str) -> Iterator[list[str]]:
    text = (files(DATA_PACKAGE) / name).read_text(encoding="utf-8")
    for row in csv.reader(text.splitlines(), delimiter="\t"):
        if row and not row[0].startswith("#"):
            yield row


def _add_folded(mapping: dict[str, object]) -> None:
    """Key a table by its folded spellings too, raw keys winning.

    Only the keys the fold actually changes are added (13,179 of the 193,350
    ``words.dat`` rows carry a ZWNJ), so this is a few per cent of extra memory
    rather than a second copy of the table.
    """
    for key, value in list(mapping.items()):
        folded = fa_script.fa_fold(key)
        if folded != key:
            mapping.setdefault(folded, value)


def _is_malformed(past: str, present: str) -> bool:
    """A ``verbs.dat`` row no paradigm should be expanded over.

    Line 1 is ``#hast`` -- an EMPTY past stem -- and without this guard it
    expands to 78 forms, among them na (frequency 277,198) and a bare noon, all
    mapping to the infinitive "-an" (judge r1 M1). Two more rows carry a literal
    " or " inside the present stem and one has a leading space (probe P-3). The
    copula needs no special case: ast and nist are stopwords.dat entries and bud
    comes from the bud#bash row.
    """
    return not past.strip() or " " in past or " " in present


class PersianLexicon:
    """The built tables. Construct with :func:`build`."""

    def __init__(
        self,
        *,
        verbs: dict[str, str],
        informal: dict[str, tuple[str, str]],
        colloquial: dict[str, str],
        compounds: frozenset[tuple[str, str]],
        tags: dict[str, tuple[str, ...]],
        stopwords: frozenset[str],
        present_stems: dict[str, str],
    ) -> None:
        self._verbs = verbs
        self._informal = informal
        self._colloquial = colloquial
        self._compounds = compounds
        self._tags = tags
        self._stopwords = stopwords
        self._present_stems = present_stems

    @property
    def verb_count(self) -> int:
        """How many formal verb forms resolve (the probe's 47,925 over the whole file)."""
        return len(self._verbs)

    def verb_form(self, word: str) -> str | None:
        """The infinitive of a formal verb form, or ``None``."""
        hit = self._verbs.get(word)
        return hit if hit is not None else self._verbs.get(fa_script.fa_fold(word))

    def informal_verb(self, word: str) -> tuple[str, str] | None:
        """``(infinitive, formal spelling)`` for a colloquial verb form, or ``None``."""
        hit = self._informal.get(word)
        return hit if hit is not None else self._informal.get(fa_script.fa_fold(word))

    def colloquial(self, word: str) -> str | None:
        """The formal spelling of a colloquial word, or ``None``."""
        hit = self._colloquial.get(word)
        return hit if hit is not None else self._colloquial.get(fa_script.fa_fold(word))

    def present_stem(self, infinitive: str) -> str | None:
        """The present stem behind an infinitive, or ``None`` when it is not a verb."""
        return self._present_stems.get(infinitive)

    def compound(self, noun: str, infinitive: str) -> bool:
        """True when *noun* + *infinitive* is a light-verb compound."""
        return (fa_script.fa_fold(noun), infinitive) in self._compounds

    def tags(self, word: str) -> tuple[str, ...]:
        """The ``words.dat`` POS tags: empty for an attested-only row and for a miss."""
        hit = self._tags.get(word)
        return hit if hit is not None else self._tags.get(fa_script.fa_fold(word), ())

    def is_attested(self, word: str) -> bool:
        """True when ``words.dat`` holds the word at all, tagged or not."""
        return word in self._tags or fa_script.fa_fold(word) in self._tags

    def is_stopword(self, word: str) -> bool:
        """True for a ``stopwords.dat`` entry."""
        return word in self._stopwords or fa_script.fa_fold(word) in self._stopwords

    def is_known_verb_form(self, word: str) -> bool:
        """The ``seperate_mi`` hook: is this spelling a FORMAL verb form?

        Formal only, as upstream: hazm builds the normaliser's verb set from
        ``verbs.dat`` alone (``normalizer.py:69``), so a colloquial spelling is
        left exactly as the user typed it and the informal tier picks it up at
        tokenize time instead.
        """
        return word in self._verbs


def _build_formal(
    verb_lines: Iterable[str],
) -> tuple[dict[str, str], dict[str, str]]:
    verbs: dict[str, str] = {}
    present_stems: dict[str, str] = {}
    for line in verb_lines:
        past, present = conjugation.split_stems(line)
        if _is_malformed(past, present):
            continue
        infinitive = conjugation.infinitive(line)
        present_stems.setdefault(infinitive, present)
        for form in conjugation.expand(past, present):
            # The first verbs.dat line wins: 2,681 forms are ambiguous across
            # homograph past stems (raft#ro "go" is line 360, raft#rub "sweep"
            # line 361), and the file's order is upstream's answer.
            verbs.setdefault(form, infinitive)
    return verbs, present_stems


def _build_informal(rows: Iterable[tuple[str, str]]) -> dict[str, tuple[str, str]]:
    informal: dict[str, tuple[str, str]] = {}
    for verb_line, informal_present in rows:
        past, present = conjugation.split_stems(verb_line)
        if _is_malformed(past, present):
            continue
        infinitive = conjugation.infinitive(verb_line)
        for pattern in conjugation.PRESENT_PATTERNS:
            form = conjugation.apply(pattern, present=informal_present)
            formal = conjugation.apply(pattern, present=present)
            informal.setdefault(form, (infinitive, formal))
            # The ZWNJ-less spelling is how people actually type it (probe P-5).
            informal.setdefault(form.replace(ZWNJ, ""), (infinitive, formal))
    return informal


def build(hazm_data: HazmData) -> PersianLexicon:
    """Build every table from one loaded pack plus the two committed TSVs."""
    verbs, present_stems = _build_formal(hazm_data.verb_lines)

    colloquial: dict[str, str] = {}
    for row in _read_tsv(COLLOQUIAL_FILE):
        if len(row) == 3 and row[0] != row[1]:
            colloquial.setdefault(row[0], row[1])
    for informal_word, formal_word in hazm_data.iwords.items():
        if informal_word != formal_word:
            colloquial.setdefault(informal_word, formal_word)

    compounds = frozenset(
        (fa_script.fa_fold(row[0]), row[1]) for row in _read_tsv(COMPOUND_VERBS_FILE) if len(row) == 2
    )

    tags = dict(hazm_data.words)
    _add_folded(tags)  # type: ignore[arg-type]
    _add_folded(colloquial)  # type: ignore[arg-type]

    lexicon = PersianLexicon(
        verbs=verbs,
        informal=_build_informal(hazm_data.iverb_rows),
        colloquial=colloquial,
        compounds=compounds,
        tags=tags,
        stopwords=hazm_data.stopwords,
        present_stems=present_stems,
    )
    # Arm the normaliser's hook last: script.py must not import this module (it
    # is imported BY it, for fa_fold), so the engine hands itself over instead.
    fa_script.FA_SEPARATE_MI_HOOK = lexicon.is_known_verb_form
    return lexicon
