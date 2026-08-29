"""Recommended downloadable zh resources (spec 10.1).

Same contract as ``services/resource_catalog.RECOMMENDED_DEFAULT_SET``: ``id``
is the PINNED on-disk slot the importer writes to, so re-downloading a title
whose name embeds a release date overwrites in place instead of forking a new
directory.

Only the dictionary is listed. The recommended frequency list (SUBTLEX-CH,
Yomitan port) is distributed through a shared dictionary folder with no stable
per-file download URL, so it stays a documented manual import — the app bundles
nothing and imports every zh resource through the same Settings flow the JA
dictionaries use.
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

ZH_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="cc-cedict",
        kind="dict",
        display_name="CC-CEDICT",
        url="https://github.com/MarvNC/cc-cedict-yomitan/releases/latest/download/CC-CEDICT.zip",
        license_note="CC-CEDICT — Creative Commons Attribution-ShareAlike 3.0; downloaded from upstream source.",
    ),
)
