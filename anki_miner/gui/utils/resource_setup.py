"""Pure, Qt-free helpers wiring a resource-download summary into config.

These functions are the unit-test seam for the first-run / Tools-menu resource
download feature. They import config + the worker's summary dataclass only — no
QtWidgets — so they can be exercised without an event loop.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from anki_miner.config import AnkiMinerConfig, ChainEntry

if TYPE_CHECKING:
    from anki_miner.gui.workers.resource_download_worker import ResourceDownloadSummary


def apply_download_summary(config: AnkiMinerConfig, summary: ResourceDownloadSummary) -> AnkiMinerConfig:
    """Return a new config reflecting the successfully-imported resources.

    Applies ONLY successful results:

    * ``dict`` → prepend an enabled indexed :class:`ChainEntry` for the new
      ``dict_id``. Idempotent: a re-run with the same dict_id moves the existing
      entry to the front (enabled) instead of stacking a duplicate, so repeated
      Tools-menu runs never grow the chain.
    * ``freq`` → set ``use_frequency_data=True`` (the worker already wrote the
      list to ``config.frequency_list_path``; the path is left unchanged).
    * ``pitch`` → set ``use_pitch_accent=True`` (path already
      ``config.pitch_accent_path``).

    If nothing succeeded, the original ``config`` object is returned unchanged.
    """
    succeeded = summary.succeeded
    if not succeeded:
        return config

    chain = list(config.dictionary_chain)
    use_frequency_data = config.use_frequency_data
    use_pitch_accent = config.use_pitch_accent

    for result in succeeded:
        if result.kind == "dict" and result.dict_id:
            # Drop any existing entry for this dict_id, then prepend a fresh
            # enabled one — idempotent and always front-of-chain.
            chain = [e for e in chain if e.dict_id != result.dict_id]
            chain.insert(0, ChainEntry(kind="indexed", dict_id=result.dict_id, enabled=True))
        elif result.kind == "freq":
            use_frequency_data = True
        elif result.kind == "pitch":
            use_pitch_accent = True

    return replace(
        config,
        dictionary_chain=tuple(chain),
        use_frequency_data=use_frequency_data,
        use_pitch_accent=use_pitch_accent,
    )


def should_offer_first_run_setup(config: AnkiMinerConfig) -> bool:
    """Return True if the user looks un-set-up (missing freq or pitch data).

    File presence is the reliable fresh-install signal: ValidationService gates
    its missing-resource warnings on ``use_frequency_data`` / ``use_pitch_accent``,
    which default False on fresh installs, so it cannot detect a fresh user. We
    check the files directly so existing users who already have resources aren't
    nagged, while fresh installs still get the offer.
    """
    return not config.frequency_list_path.is_file() or not config.pitch_accent_path.is_file()
