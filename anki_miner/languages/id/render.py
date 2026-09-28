"""Indonesian card render hooks (spec C.5): Root, Affixes and Formal form.

Root and Affixes come from the dictionary's own etymology line (wty-id-en: ``From meng- + beli``,
``ke- + adab + -an``), never from the deinflection ladder's guess, and are blank when the entry
has none. The affixes print as the dictionary writes them (``meng- + rugi + -kan``). The formal
form is the colloquial table's spelling for a colloquial front (``nggak`` -> ``tidak``).
"""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING, Any

from anki_miner.languages.id.colloquial import ID_COLLOQUIAL
from anki_miner.languages.id.morphology import id_fold

if TYPE_CHECKING:  # annotation-only, the ko/render.py pattern
    from anki_miner.config.config import AnkiMinerConfig

_ETYMOLOGY_RE = re.compile(r'data-sc-content="Etymology-content"[^>]*>(.*?)</div>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_GLOSS = r"(?:\s*\([^()]*\))?"
_COMPONENT = r"-?[a-z]+(?:-[a-z]+)*-?(?:\s+-[a-z]+)?"
_MARKER = (
    r"[Aa]ffixed(?: from| of)?|[Aa]ffixation(?: of)?|[Ee]quivalent to|[Aa]naly[sz]ed as|[Pp]refixed|[Ss]uffixed|"
    r"[Ii]nfixed(?: from)?|[Cc]ompound of|[Cc]ombination of|[Rr]eanaly[sz]ed as|surface analysis,|[Cc]onstructed"
)
#: A bare lead (block start, sentence break, ``from``) or an explicit marker, which names the analysis outright.
_LEAD = rf"(?:^|(?<=[.;:,]\s)|\b[Ff]rom\s+|\b(?P<marker>{_MARKER})\s+)"
#: The formula ends at a word end: ``ka- + göm`` never yields the root ``g``.
_FORMULA_RE = re.compile(_LEAD + rf"(?P<formula>{_COMPONENT}{_GLOSS}(?:\s*\+\s*{_COMPONENT}{_GLOSS})+)(?![^\W\d_])")
#: The first step into another language (``from Latin``, ``from Old Javanese``, ``from Proto-Malayic``); a Malay or
#: Indonesian step (``From Malay pilihan``, ``from Classical Malay``) is still the word's own history, and a doubled
#: ``From From meng- + ...`` is not a language.
_FOREIGN_STEP_RE = re.compile(r"\b[Ff]rom\s+(?!(?:[A-Z][a-z]+\s+)*(?:Malay|Indonesian)\b|From\b)[A-Z]")
_GLOSS_RE = re.compile(r"\s*\([^()]*\)")


def etymology_parse(definition_html: str) -> tuple[str, str] | None:
    """``(root, affixes)`` from the first etymology formula with exactly one bare word and an affix.

    A formula behind a bare lead counts only before the block's first foreign step: past it, ``from co- + ops``
    (kopi, via Latin) or ``from relation + -ship`` (a note on English *ship*) is another language's analysis.
    An explicit marker (``Equivalent to``, ``Affixed``) counts anywhere.
    """
    for block in _ETYMOLOGY_RE.findall(definition_html or ""):
        text = " ".join(html.unescape(_TAG_RE.sub(" ", block)).split())
        foreign = _FOREIGN_STEP_RE.search(text)
        own_history_end = foreign.start() if foreign else len(text)
        for match in _FORMULA_RE.finditer(text):
            if match.group("marker") is None and match.start() >= own_history_end:
                continue
            parts = [_GLOSS_RE.sub("", part).strip() for part in match.group("formula").split("+")]
            pieces = [piece for part in parts for piece in part.split()]
            prefixes = [p for p in pieces if p.endswith("-") and not p.startswith("-")]
            suffixes = [p for p in pieces if p.startswith("-")]
            roots = [p for p in pieces if not p.startswith("-") and not p.endswith("-")]
            if len(roots) == 1 and (prefixes or suffixes):
                return roots[0], " + ".join(prefixes + roots + suffixes)
    return None


class RootAffixHook:
    """``root`` and ``affixes`` from the entry's etymology; nothing when it has no affix formula.

    The card can show another word's entry: a form (``dibeli``, ``kulakukan``) reads the entry it names or the
    ladder reaches (``membeli``, ``melakukan``), whose ``meng- + beli`` is not the front's analysis. So a formula
    with a prefix counts only when the front, or a colloquial front's formal spelling (``ngerti`` -> ``mengerti``),
    opens with one of its prefixes; two letters cover the allomorphs (``mem-``, ``meny-``, ``be-``, ``bel-``).
    """

    def field_names(self) -> tuple[str, ...]:
        return ("root", "affixes")

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        del config  # the mapped field name is the switch; no setting gates it
        parsed = etymology_parse(str(getattr(word, "definition_html", "") or ""))
        if parsed is None:
            return {}
        root, affixes = parsed
        openings = tuple(p[:-1][:2] for p in affixes.split(" + ") if p.endswith("-") and not p.startswith("-"))
        front = id_fold(str(getattr(word, "mined_form", "") or ""))
        if openings and not any(form.startswith(openings) for form in (front, ID_COLLOQUIAL.get(front, ""))):
            return {}
        return {"root": root, "affixes": affixes}


class FormalFormHook:
    """``formal_form``: the colloquial table's formal spelling when it differs from the front."""

    def field_names(self) -> tuple[str, ...]:
        return ("formal_form",)

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        del config
        front = str(getattr(word, "mined_form", "") or "")
        formal = ID_COLLOQUIAL.get(front, "")
        return {"formal_form": formal} if formal and formal != front else {}
