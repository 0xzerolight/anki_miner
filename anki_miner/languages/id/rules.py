"""Indonesian deinflection candidates (spec C.5): the ladder the dictionary validates.

Written from the published confix-stripping rules (Nazief-Adriani, Asian 2007) as Sastrawi
documents them, and Wiktionary's Indonesian affix appendix. No Sastrawi code and no root list is
copied (PySastrawi's Kateglo list is CC-BY-NC-SA, spec C.5 Rejected): every candidate is only a
spelling to try, and ``DefinitionService`` keeps the first one the installed dictionary knows, so
a word that is itself a headword (``mengerti``, ``belajar``) never reaches this ladder and a
candidate past it (``erti``) is never shown.

Order, fewest steps first: the colloquial table (N0) -> reduplication (R1-R3; unequal halves such
as ``sayur-mayur`` are never split) -> particles ``-lah -kah -tah -pun`` -> clitics ``-nya -ku -mu``
-> suffixes ``-kan -an -i`` and colloquial ``-in`` -> one prefix layer (``di- ku- kau-`` passives
first offer the active form, ``dibeli`` -> ``membeli``) -> confixes -> two deeper prefix layers ->
vowel variants (``bener`` -> ``benar``, ``pake`` -> ``pakai``, ``tau`` -> ``tahu``). Capped at
:data:`MAX_CANDIDATES`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

VOWELS = frozenset("aiueo")
PARTICLES = ("lah", "kah", "tah", "pun")
CLITICS = ("nya", "ku", "mu")
MAX_CANDIDATES = 20
MIN_STEM = 2

#: Prefix/suffix pairs a confix never combines (Sastrawi's disallowed pairs: ``be-...-i``, ``ke-...-kan``).
INVALID_CONFIXES = frozenset(
    {("be", "i"), ("di", "an"), ("ke", "i"), ("ke", "kan"), ("me", "an"), ("se", "i"), ("se", "kan"), ("te", "an")}
)

_X2 = re.compile(r"^([^\W\d_]+)2(nya|ku|mu)?$")


def _vowel_at(text: str, index: int) -> bool:
    return len(text) > index and text[index] in VOWELS


def active_form(root: str) -> str:
    """meN- + *root* with nasal assimilation (beli -> membeli, kirim -> mengirim, sapu -> menyapu)."""
    if not root:
        return ""
    head = root[0]
    if _vowel_at(root, 1) and head in "kpts":
        return {"k": "meng", "p": "mem", "t": "men", "s": "meny"}[head] + root[1:]
    if head in VOWELS or head in "gh":
        return "meng" + root
    if head in "bfv":
        return "mem" + root
    if head in "cdjz":
        return "men" + root
    if head in "lmnrwy":
        return "me" + root
    return ""


def _nasal(rest: str, nasal: str) -> list[str]:
    """Root candidates behind a meN-/peN- nasal, best first."""
    if not rest:
        return []
    head = rest[0]
    if nasal == "ng":
        if head in VOWELS:
            return [rest, "k" + rest]
        return [rest] if head in "gh" or rest.startswith("kh") else []
    if nasal == "ny":
        return ["s" + rest, "ny" + rest] if head in VOWELS else []
    if nasal == "n":
        if head in "cdjz":
            return [rest]
        return ["t" + rest] if head in VOWELS else []
    if nasal == "m":
        if head in "bfvp":
            return [rest]
        return ["p" + rest, "m" + rest] if head in VOWELS else []
    return []


#: One stripped layer: (family, stems). A prefix family keys :data:`INVALID_CONFIXES`.
Hit = tuple[str, list[str]]


def prefix_layer(w: str) -> list[Hit]:
    """Every prefix one layer can remove from *w*: formal P1-P15, then colloquial C1-C5."""
    hits: list[Hit] = []

    def add(family: str, *texts: str) -> None:
        kept = [t for t in texts if len(t) >= MIN_STEM]
        if kept:
            hits.append((family, kept))

    for prefix in ("kau", "di", "ku"):
        if w.startswith(prefix) and len(w) - len(prefix) >= 3:
            rest = w[len(prefix) :]
            add("di", active_form(rest), rest)
            break
    for prefix in ("ke", "se"):
        if w.startswith(prefix) and len(w) - len(prefix) >= 3:
            add(prefix, w[len(prefix) :])
    for family in ("me", "pe"):
        if w.startswith(family + "nge") and len(w) > 6:
            add(family, w[5:], "ke" + w[5:])
        if w.startswith(family + "ng"):
            add(family, *_nasal(w[4:], "ng"))
        elif w.startswith(family + "ny"):
            add(family, *_nasal(w[4:], "ny"))
        elif w.startswith(family + "n"):
            add(family, *_nasal(w[3:], "n"))
        elif w.startswith(family + "m"):
            add(family, *_nasal(w[3:], "m"))
        elif w.startswith(family) and w[2:3] and w[2] in "lmnrwy":
            add(family, w[2:])
    if w.startswith("per"):
        add("pe", w[3:])
    if w.startswith("pel") and _vowel_at(w, 3):
        add("pe", w[3:])
    if w.startswith("ber") or (w.startswith("bel") and _vowel_at(w, 3)):
        add("be", w[3:])
    elif w.startswith("be"):
        add("be", w[2:])
    if w.startswith("ter"):
        add("te", w[3:])
    if len(w) >= 5:
        if w.startswith("nge"):
            add("me", w[3:])
        if w.startswith("ng") and _vowel_at(w, 2):
            add("me", w[2:], "k" + w[2:])
        elif w.startswith("ny") and _vowel_at(w, 2):
            add("me", "s" + w[2:], "c" + w[2:])
        elif w.startswith("m") and _vowel_at(w, 1) and w[1] != "e":  # me- is the formal prefix above
            add("me", "p" + w[1:])
        elif w.startswith("n") and _vowel_at(w, 1):
            add("me", "t" + w[1:])
        if w.startswith("ke"):
            add("te", "ter" + w[2:])
    return hits


def _strip(word: str, ending: str) -> str:
    if word.endswith(ending) and len(word) - len(ending) >= MIN_STEM + 1:
        return word[: -len(ending)]
    return ""


def suffix_layer(word: str) -> list[Hit]:
    """S3 ``-kan -an -i`` and colloquial S4 ``-in`` (rest, rest+kan, rest+i, filed under ``kan``)."""
    hits: list[Hit] = [(suffix, [stem]) for suffix in ("kan", "an", "i") if (stem := _strip(word, suffix))]
    stem = _strip(word, "in")
    if stem and len(word) > 4:
        hits.append(("kan", [stem, stem + "kan", stem + "i"]))
    return hits


def reduplication_candidates(word: str) -> list[str]:
    """R1 ``X-X`` -> X; R2 ``X-Xan``/``X-Xnya`` and ``ber/se/meN + X-X`` -> X; R3 ``X2`` -> ``X-X``; R4 never split."""
    out: list[str] = []
    match = _X2.match(word)
    if match:
        base, clitic = match.group(1), match.group(2) or ""
        out += [f"{base}-{base}{clitic}", f"{base}-{base}", base] if clitic else [f"{base}-{base}", base]
    parts = word.split("-")
    if len(parts) == 2 and all(parts):
        left, right = parts
        if left == right or (
            right.startswith(left) and right[len(left) :] in ("an", "nya", "kan", "i", "ku", "mu", "lah")
        ):
            out.append(left)
        elif left.endswith(right):
            out.append(right)
        else:
            for ending in (*CLITICS, "an", "lah"):
                core = _strip(right, ending)
                if core and left.endswith(core):
                    out.append(core)
                    break
    return out


def vowel_variants(word: str) -> list[str]:
    """V1 final-syllable e -> a (bener, simpen); V2 e$ -> ai, o$ -> au, and an h restored (abis, tau, liat)."""
    out: list[str] = []
    last_e = word.rfind("e")
    if last_e > 0 and last_e >= len(word) - 3 and not word.endswith("e"):
        out.append(word[:last_e] + "a" + word[last_e + 1 :])
    if word.endswith("e"):
        out.append(word[:-1] + "ai")
    if word.endswith("o"):
        out.append(word[:-1] + "au")
    if word[:1] in VOWELS and len(word) >= 4:
        out.append("h" + word)
    for i in range(1, len(word)):
        if word[i] in VOWELS and word[i - 1] in VOWELS and word[i - 1] != word[i]:
            out.append(word[:i] + "h" + word[i:])
            break
    return out


def deinflection_candidates(word: str, formal_of: Callable[[str], str | None]) -> list[str]:
    """The ladder for *word* (already folded), fewest steps first, never *word* itself, capped."""
    out: dict[str, None] = {}

    def add(texts: Iterable[str]) -> None:
        for text in texts:
            if text and text != word:
                out.setdefault(text, None)

    formal = formal_of(word)
    if formal:
        add([formal])
    add(reduplication_candidates(word))
    inflected = [word]
    stem = next((s for s in (_strip(word, p) for p in PARTICLES) if s), "")
    if stem:
        inflected.append(stem)
    for form in list(inflected):
        stem = next((s for s in (_strip(form, c) for c in CLITICS) if s), "")
        if stem:
            inflected.append(stem)
    add(inflected)
    suffixed = [(suffix, stem) for form in inflected for suffix, stems in suffix_layer(form) for stem in stems]
    add(stem for _, stem in suffixed)
    bare = [(family, stem) for form in inflected for family, stems in prefix_layer(form) for stem in stems]
    add(stem for _, stem in bare)
    confix = [
        stem
        for suffix, form in suffixed
        for family, stems in prefix_layer(form)
        if (family, suffix) not in INVALID_CONFIXES
        for stem in stems
    ]
    add(confix)
    layer = [stem for _, stem in bare] + confix
    for _ in range(2):
        layer = [stem for form in layer for _, stems in prefix_layer(form) for stem in stems]
        add(layer)
    add(vowel_variants(word))
    for form in inflected[1:]:
        add(vowel_variants(form))
    return list(out)[:MAX_CANDIDATES]
