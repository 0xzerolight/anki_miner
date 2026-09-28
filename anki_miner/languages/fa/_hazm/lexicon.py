"""Every Persian table the tokenizer asks, built once from the pack and the TSVs.

The formal verb table is the 79 patterns expanded over ``verbs.dat``, plus the
mi-/nami- forms of its preverb verbs spelt bar-mi-gardam; the informal one is
the 30 present patterns expanded over ``iverbs.dat``'s informal stems, paired
form for form with the formal spelling they stand for; the colloquial-ending one
is the -e/-an/-in presents over the same stems. hazm's own
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
    from collections.abc import Callable, Iterable, Iterator

#: Where the committed tables live (package data, both build paths).
DATA_PACKAGE = "anki_miner.languages.fa.data"
COMPOUND_VERBS_FILE = "compound_verbs.tsv"
COLLOQUIAL_FILE = "colloquial.tsv"
PREFERRED_PRESENT_STEMS_FILE = "preferred_present_stems.tsv"
VERB_FIRST_FILE = "verb_first.tsv"

ZWNJ = "\N{ZERO WIDTH NON-JOINER}"

#: The seated hamzas flattened onto their bare seat: the spelling subtitles use
#: (soal 9,562 in fa_50k against so'al's 1,622). ``fa_fold`` keeps them apart on
#: purpose -- it is the dictionary key -- so only the word table learns the alias.
_SEAT_FLAT = str.maketrans(
    {
        "\N{ARABIC LETTER WAW WITH HAMZA ABOVE}": "\N{ARABIC LETTER WAW}",
        "\N{ARABIC LETTER YEH WITH HAMZA ABOVE}": "\N{ARABIC LETTER FARSI YEH}",
        "\N{ARABIC LETTER ALEF WITH HAMZA ABOVE}": "\N{ARABIC LETTER ALEF}",
        "\N{ARABIC LETTER ALEF WITH HAMZA BELOW}": "\N{ARABIC LETTER ALEF}",
    }
)

#: Colloquial-ending forms another word owns first, measured over fa_50k:
#: beshin is "sit!" (neshastan) far more often than "that you become"
#: (shodan), and xare is "the donkey" (xar + the spoken definite -e) once the
#: normaliser has joined mi- xare into one word.
_NOT_COLLOQUIAL_ENDINGS = frozenset(
    {
        "\N{ARABIC LETTER BEH}\N{ARABIC LETTER SHEEN}\N{ARABIC LETTER FARSI YEH}\N{ARABIC LETTER NOON}",
        "\N{ARABIC LETTER KHAH}\N{ARABIC LETTER REH}\N{ARABIC LETTER HEH}",
    }
)


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


def _hamza_spellings(tags: dict[str, tuple[str, ...]]) -> dict[str, str]:
    """``flat spelling -> hamza spelling`` for every TAGGED row a seated hamza sets apart.

    words.dat holds the flat spellings too, but as untagged count-0 rows (soal,
    bel-akhare, ma'mur), so an alias kept only where no tagged row answers the
    flat key never overrides one: reyis is tagged in its own right and stays.
    """
    spellings: dict[str, str] = {}
    for key, key_tags in tags.items():
        flat = key.translate(_SEAT_FLAT)
        if key_tags and flat != key and not tags.get(flat) and not tags.get(fa_script.fa_fold(flat)):
            spellings.setdefault(flat, key)
    return spellings


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
        colloquial_endings: dict[str, tuple[str, str]],
        colloquial: dict[str, str],
        compounds: frozenset[tuple[str, str]],
        tags: dict[str, tuple[str, ...]],
        stopwords: frozenset[str],
        present_stems: dict[str, str],
        verb_first: frozenset[str],
        hamza_spellings: dict[str, str],
    ) -> None:
        self._verbs = verbs
        self._informal = informal
        self._colloquial_endings = colloquial_endings
        self._colloquial = colloquial
        self._compounds = compounds
        self._tags = tags
        self._stopwords = stopwords
        self._present_stems = present_stems
        self._verb_first = verb_first
        self._hamza_spellings = hamza_spellings

    @property
    def verb_count(self) -> int:
        """How many formal verb forms resolve (49,331 over the whole file, preverb forms included)."""
        return len(self._verbs)

    def verb_form(self, word: str) -> str | None:
        """The infinitive of a formal verb form, or ``None``."""
        hit = self._verbs.get(word)
        return hit if hit is not None else self._verbs.get(fa_script.fa_fold(word))

    def informal_verb(self, word: str) -> tuple[str, str] | None:
        """``(infinitive, formal spelling)`` for a colloquial verb form, or ``None``."""
        hit = self._informal.get(word)
        return hit if hit is not None else self._informal.get(fa_script.fa_fold(word))

    def colloquial_ending_verb(self, word: str) -> tuple[str, str] | None:
        """``(infinitive, formal spelling)`` for a -e/-an/-in present (dare, mikonan), or ``None``."""
        hit = self._colloquial_endings.get(word)
        return hit if hit is not None else self._colloquial_endings.get(fa_script.fa_fold(word))

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

    def hamza_spelling(self, word: str) -> str | None:
        """The tagged hamza spelling of a flat one (soal -> so'al), or ``None``.

        Never answers for a word with a tagged row of its own (``_hamza_spellings``).
        """
        hit = self._hamza_spellings.get(word)
        return hit if hit is not None else self._hamza_spellings.get(fa_script.fa_fold(word))

    def is_verb_first(self, word: str) -> bool:
        """True for a ``verb_first.tsv`` surface: its verb reading answers before its tagged row."""
        return fa_script.fa_fold(word) in self._verb_first

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
    iverb_rows: Iterable[tuple[str, str]],
    preferred_stems: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    verbs: dict[str, str] = {}
    present_stems: dict[str, str] = {}
    stems: list[tuple[str, str]] = []
    for line in verb_lines:
        past, present = conjugation.split_stems(line)
        if _is_malformed(past, present):
            continue
        stems.append((past, present))
        infinitive = conjugation.infinitive(line)
        # First line wins here too, except where verbs.dat lists a rarer stem
        # first (nevesht#navard before nevesht#nevis, bud#ast before bud#bash):
        # the committed override names the stem a learner conjugates on, and
        # the Present stem field exists to teach exactly that.
        present_stems.setdefault(infinitive, preferred_stems.get(infinitive, present))
        for form in conjugation.expand(past, present):
            # The first verbs.dat line wins: 2,681 forms are ambiguous across
            # homograph past stems (raft#ro "go" is line 360, raft#rub "sweep"
            # line 361), and the file's order is upstream's answer.
            verbs.setdefault(form, infinitive)

    # A preverb verb whose remainder is itself a verbs.dat verb (bar + gasht#gard,
    # 33 rows) also takes mi-/nami- AFTER the preverb: bar-mi-gardam, which the
    # plain templates never build (they give mi-bargardam). The normaliser only
    # restores a word-initial mi-'s ZWNJ, so the joined spelling is keyed too.
    known = set(stems)
    for past, present in stems:
        split = conjugation.split_preverb(past, present)
        if split is None:
            continue
        preverb, rest_past, rest_present = split
        if (rest_past, rest_present) not in known:
            continue
        infinitive = conjugation.apply(conjugation.INFINITIVE_PATTERN, past=past)
        for form in conjugation.expand_preverb(preverb, rest_past, rest_present):
            verbs.setdefault(form, infinitive)
            verbs.setdefault(form.replace(ZWNJ, ""), infinitive)

    # An iverbs.dat row whose informal stem IS the formal one (bud#bash bash,
    # kard#kon kon: seven rows) adds no colloquial spelling, so the informal
    # table skips it. What the row does say is which verb owns a homograph
    # present stem: bash- is budan's though bashid#bash (line 117) precedes
    # bud#bash (144), kesh- keshidan's though kosht#kesh precedes keshid#kesh.
    # Those forms resolved through the informal tier before; they keep the verb.
    for verb_line, informal_present in iverb_rows:
        past, present = conjugation.split_stems(verb_line)
        if informal_present != present or _is_malformed(past, present):
            continue
        infinitive = conjugation.infinitive(verb_line)
        for pattern in conjugation.PRESENT_PATTERNS:
            verbs[conjugation.apply(pattern, present=present)] = infinitive
    return verbs, present_stems


def _build_informal(
    rows: Iterable[tuple[str, str]],
    expand: Callable[[str, str], Iterable[tuple[str, str]]],
) -> dict[str, tuple[str, str]]:
    """``form -> (infinitive, formal spelling)`` over the ``iverbs.dat`` rows."""
    informal: dict[str, tuple[str, str]] = {}
    for verb_line, informal_present in rows:
        past, present = conjugation.split_stems(verb_line)
        if _is_malformed(past, present):
            continue
        infinitive = conjugation.infinitive(verb_line)
        for form, formal in expand(informal_present, present):
            informal.setdefault(form, (infinitive, formal))
            # The ZWNJ-less spelling is how people actually type it (probe P-5).
            informal.setdefault(form.replace(ZWNJ, ""), (infinitive, formal))
    return informal


def _informal_presents(informal_present: str, present: str) -> list[tuple[str, str]]:
    # A row whose informal stem is the formal one would pair every formal form
    # with itself and tag the textbook spelling Informal; _build_formal keeps
    # its verb instead.
    if informal_present == present:
        return []
    return [
        (conjugation.apply(pattern, present=informal_present), conjugation.apply(pattern, present=present))
        for pattern in conjugation.PRESENT_PATTERNS
    ]


def build(hazm_data: HazmData) -> PersianLexicon:
    """Build every table from one loaded pack plus the committed TSVs."""
    preferred_stems = {row[0]: row[1] for row in _read_tsv(PREFERRED_PRESENT_STEMS_FILE) if len(row) == 2}
    verbs, present_stems = _build_formal(hazm_data.verb_lines, hazm_data.iverb_rows, preferred_stems)

    colloquial_endings = _build_informal(hazm_data.iverb_rows, conjugation.expand_colloquial)
    for form in _NOT_COLLOQUIAL_ENDINGS:
        colloquial_endings.pop(form, None)

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
        informal=_build_informal(hazm_data.iverb_rows, _informal_presents),
        colloquial_endings=colloquial_endings,
        colloquial=colloquial,
        compounds=compounds,
        tags=tags,
        stopwords=hazm_data.stopwords,
        present_stems=present_stems,
        verb_first=frozenset(fa_script.fa_fold(row[0]) for row in _read_tsv(VERB_FIRST_FILE)),
        # Built over the folded keys as well, so a ZWNJ-less spelling finds its row.
        hamza_spellings=_hamza_spellings(tags),
    )
    # Arm the normaliser's hook last: script.py must not import this module (it
    # is imported BY it, for fa_fold), so the engine hands itself over instead.
    fa_script.FA_SEPARATE_MI_HOOK = lexicon.is_known_verb_form
    return lexicon
