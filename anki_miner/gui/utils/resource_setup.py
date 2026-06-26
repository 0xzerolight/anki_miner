"""Pure, Qt-free helpers wiring a resource-download summary into config.

These functions are the unit-test seam for the first-run / Tools-menu resource
download feature. They import config + the worker's summary dataclass only — no
QtWidgets — so they can be exercised without an event loop.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from anki_miner.config import AnkiMinerConfig, ChainEntry, FreqEntry

if TYPE_CHECKING:
    from anki_miner.gui.workers.resource_download_worker import ResourceDownloadSummary


def apply_download_summary(config: AnkiMinerConfig, summary: ResourceDownloadSummary) -> AnkiMinerConfig:
    """Return a new config reflecting the successfully-imported resources.

    Applies ONLY successful results:

    * ``dict`` → prepend an enabled indexed :class:`ChainEntry` for the new
      ``dict_id``. Idempotent: a re-run with the same dict_id moves the existing
      entry to the front (enabled) instead of stacking a duplicate, so repeated
      Tools-menu runs never grow the chain.
    * ``freq`` → prepend an enabled :class:`FreqEntry` for the new ``source_id``
      (the worker imported the source into ``config.freqs_root/<source_id>/``)
      and set ``use_frequency_data=True``. Idempotent in the same way as the
      dict path: a re-run with the same source_id moves the existing entry to
      the front instead of duplicating it. Adding the chain entry is what makes
      the freshly-downloaded frequency data live in the same session — flipping
      the flag alone leaves an empty chain → no providers.
    * ``pitch`` → set ``use_pitch_accent=True`` (path already
      ``config.pitch_accent_path``).

    If nothing succeeded, the original ``config`` object is returned unchanged.
    """
    succeeded = summary.succeeded
    if not succeeded:
        return config

    chain = list(config.dictionary_chain)
    freq_chain = list(config.frequency_chain)
    use_frequency_data = config.use_frequency_data
    use_pitch_accent = config.use_pitch_accent

    for result in succeeded:
        if result.kind == "dict" and result.dict_id:
            # Drop any existing entry for this dict_id, then prepend a fresh
            # enabled one — idempotent and always front-of-chain.
            chain = [e for e in chain if e.dict_id != result.dict_id]
            chain.insert(0, ChainEntry(kind="indexed", dict_id=result.dict_id, enabled=True))
        elif result.kind == "freq" and result.source_id:
            # Mirror the dict path: drop any existing entry for this source_id,
            # then prepend a fresh enabled one (idempotent, front-of-chain).
            # Without this the chain stays empty and the flipped flag yields zero
            # frequency providers until an app restart.
            freq_chain = [e for e in freq_chain if e.source_id != result.source_id]
            freq_chain.insert(0, FreqEntry(source_id=result.source_id, enabled=True))
            use_frequency_data = True
        elif result.kind == "pitch":
            use_pitch_accent = True

    return replace(
        config,
        dictionary_chain=tuple(chain),
        frequency_chain=tuple(freq_chain),
        use_frequency_data=use_frequency_data,
        use_pitch_accent=use_pitch_accent,
    )
