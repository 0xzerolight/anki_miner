"""th card render hooks (Paiboon reading, classifier). Filled in Task 10.

The tuple exists here so ``build_profile`` names one symbol whose identity never
changes; Task 10 replaces the empty tuple with the two real hooks and adds the
regexes that read the wty-th-en Grammar line and the Volubilis gloss bracket.
"""

from __future__ import annotations

from anki_miner.languages.profile import CardRenderHook

TH_RENDER_HOOKS: tuple[CardRenderHook, ...] = ()
