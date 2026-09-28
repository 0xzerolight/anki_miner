"""Lithuanian SubtitleParser factory: the shared spaced factory plus the form-of front repair (``_spaced/form_of.py``).

wty-lt-en keys every lemma row unstressed but writes many form-row targets with stress marks, often
beside the plain spelling (``paliko`` names ``palikti`` and ``pali̇̀kti``), so the targets are read
through the same stress fold as the keys (plan D-1): the two collapse to one and the card front
carries no marks.

The it-style attested-lemma pass was measured and left out: on ALKSNIS dev+test it fires on 426 of
11,987 content tokens for a net +17 correct lemmas (plan 2026-09-17-lt, D-7).
"""

from __future__ import annotations

from typing import Any

from anki_miner.languages._spaced.form_of import lemma_row_targets
from anki_miner.languages.lt.morphology import strip_stress_marks


def unstressed_row_targets(content: str, tags: str) -> list[str] | None:
    """``lemma_row_targets`` with the stress marks off every target."""
    targets = lemma_row_targets(content, tags)
    return None if targets is None else [strip_stress_marks(target) for target in targets]


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages._spaced.form_of import FormOfLemmaPass

    kwargs.setdefault("token_post_pass", FormOfLemmaPass(row_targets=unstressed_row_targets))
    return create_spaced_parser(config, **kwargs)
