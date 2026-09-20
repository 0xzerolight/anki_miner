"""Can this machine mine Arabic? Only when the calima-msa-r13 database pack is on disk.

The analyzer is in-tree, so the one requirement is data: no pip package carries it (no ``[ar]`` extra),
and every install, frozen or pip, downloads the same pack from Settings -> Mining Language.
"""

from __future__ import annotations

#: The component ``languages/ar/pack.py`` installs, and the file the analyzer reads inside it.
AR_DB_COMPONENT = "calima_msa"
AR_DB_FILE = "morphology.db"
AR_PACK_REASON = "Arabic mining needs the Arabic language pack. Download it in Settings -> Mining Language."


def ar_missing_reason() -> str | None:
    """The profile's ``unavailable_reason``: a stat call, nothing imported or loaded."""
    from anki_miner.services.language_pack_installer import component_path

    return None if component_path("ar", AR_DB_COMPONENT) is not None else AR_PACK_REASON
