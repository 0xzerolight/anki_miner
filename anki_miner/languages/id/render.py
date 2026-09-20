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

if TYPE_CHECKING:  # annotation-only, the ko/render.py pattern
    from anki_miner.config.config import AnkiMinerConfig

_ETYMOLOGY_RE = re.compile(r'data-sc-content="Etymology-content"[^>]*>(.*?)</div>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_GLOSS = r"(?:\s*\([^()]*\))?"
_COMPONENT = r"-?[a-z]+(?:-[a-z]+)*-?(?:\s+-[a-z]+)?"
_LEAD = (
    r"(?:^|(?<=[.;:,]\s)|\b(?:[Ff]rom|[Aa]ffixed(?: from| of)?|[Aa]ffixation(?: of)?|[Ee]quivalent to|"
    r"[Aa]naly[sz]ed as|[Pp]refixed|[Ss]uffixed|[Cc]ompound of|[Cc]ombination of|[Rr]eanaly[sz]ed as|"
    r"surface analysis,|[Cc]onstructed)\s+)"
)
_FORMULA_RE = re.compile(_LEAD + rf"({_COMPONENT}{_GLOSS}(?:\s*\+\s*{_COMPONENT}{_GLOSS})+)")
_GLOSS_RE = re.compile(r"\s*\([^()]*\)")


def etymology_parse(definition_html: str) -> tuple[str, str] | None:
    """``(root, affixes)`` from the first etymology formula with exactly one bare word and an affix."""
    for block in _ETYMOLOGY_RE.findall(definition_html or ""):
        text = " ".join(html.unescape(_TAG_RE.sub(" ", block)).split())
        for match in _FORMULA_RE.finditer(text):
            parts = [_GLOSS_RE.sub("", part).strip() for part in match.group(1).split("+")]
            pieces = [piece for part in parts for piece in part.split()]
            prefixes = [p for p in pieces if p.endswith("-") and not p.startswith("-")]
            suffixes = [p for p in pieces if p.startswith("-")]
            roots = [p for p in pieces if not p.startswith("-") and not p.endswith("-")]
            if len(roots) == 1 and (prefixes or suffixes):
                return roots[0], " + ".join(prefixes + roots + suffixes)
    return None


class RootAffixHook:
    """``root`` and ``affixes`` from the entry's etymology; nothing when it has no affix formula."""

    def field_names(self) -> tuple[str, ...]:
        return ("root", "affixes")

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        del config  # the mapped field name is the switch; no setting gates it
        parsed = etymology_parse(str(getattr(word, "definition_html", "") or ""))
        if parsed is None:
            return {}
        root, affixes = parsed
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
