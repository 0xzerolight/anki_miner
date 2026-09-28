"""Croatian SubtitleParser factory: the shared spaced factory, the short-infinitive repair, then the form-of repair.

The form-of front repair (``_spaced/form_of.py``) runs second, so a short infinitive the first pass has
already completed (``radit`` -> ``raditi``) is a lemma with a lemma row and stays as it is. It repairs the
dialogue forms the model lemmatises as themselves or as another class: first and second person present and
imperatives (``Čekam`` tagged a feminine noun -> ``čekati``, ``Uzmi`` -> ``uzeti``, ``idemo`` -> ``ići``). On 135
dialogue lines it takes the wrong fronts from 71 of 319 parsed words to 28 of 313; the two right fronts it
changes move to the dictionary's own headword (``prekasan`` -> ``prekasno``, ``naočala`` -> ``naočale``). wty-sh-en
keys every lemma row plain but names about 1,000 targets with a tone mark (``preveo`` names ``prèvesti``), so
the targets are read through the same tone fold as the aspect partners and the card front carries no mark.
"""

from __future__ import annotations

from typing import Any

from anki_miner.languages._spaced.form_of import lemma_row_targets
from anki_miner.languages.hr.morphology import hr_tone_fold


def toneless_row_targets(content: str, tags: str) -> list[str] | None:
    """``lemma_row_targets`` with the tone marks off every target."""
    targets = lemma_row_targets(content, tags)
    return None if targets is None else [hr_tone_fold(target) for target in targets]


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages._spaced.form_of import FormOfLemmaPass, OrderedPasses
    from anki_miner.languages.hr.morphology import hr_short_infinitive_pass

    kwargs.setdefault(
        "token_post_pass",
        OrderedPasses(hr_short_infinitive_pass, FormOfLemmaPass(row_targets=toneless_row_targets)),
    )
    return create_spaced_parser(config, **kwargs)
