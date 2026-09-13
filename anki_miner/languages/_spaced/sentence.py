"""SentenceRules for space-delimited Latin text (reading-tab loaders, S8)."""

from __future__ import annotations

from anki_miner.languages.profile import SentenceRules

LATIN_TERMINATORS: frozenset[str] = frozenset(".!?‼⁉⁇⁈")


def sentence_rules(abbreviations: frozenset[str] = frozenset()) -> SentenceRules:
    """Latin rules; ``abbreviations`` are casefolded keys without their final dot (``dr``, ``z.b``).

    Openers/closers are the unambiguous pairs only: ASCII ``"`` opens and closes
    alike and ``’`` is also the apostrophe, so neither may move bracket depth.
    """
    return SentenceRules(
        terminators=LATIN_TERMINATORS,
        ellipses=frozenset("…"),
        openers=frozenset("([{“«"),
        closers=frozenset(")]}”»"),
        space_aware=True,
        abbreviations=abbreviations,
    )
