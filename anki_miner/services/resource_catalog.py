"""Recommended downloadable resources — single source of truth.

Pure data, no I/O. The URLs here mirror README's "Recommended Resources"
table; keep the two in sync. A later Qt worker iterates
:data:`RECOMMENDED_DEFAULT_SET`, downloads each ``url`` via
``resource_downloader.download_to_temp``, then routes the temp file to the
right importer based on ``kind`` (``dict`` → Yomitan importer, ``freq`` →
Yomitan frequency importer, ``pitch`` → direct file write).
"""

from dataclasses import dataclass

# Allowed values for ResourceSpec.kind; the download worker dispatches on these.
RESOURCE_KINDS: frozenset[str] = frozenset({"dict", "freq", "pitch"})


@dataclass(frozen=True)
class ResourceSpec:
    """Describes one recommended downloadable resource.

    Attributes:
        id: Stable short identifier (e.g. ``"jmdict-english"``).
        kind: One of ``"dict"``, ``"freq"``, or ``"pitch"``.
        display_name: Human-facing name shown in the UI.
        url: Direct download URL for the resource artifact.
        license_note: Short human string about source/license.
    """

    id: str
    kind: str
    display_name: str
    url: str
    license_note: str


# For dict resources, ``id`` is the PINNED on-disk slot the importer writes to
# (dicts_root/<id>/), so re-downloading a title that embeds a changing release
# date overwrites in place instead of forking a new directory. Pick a stable,
# collision-free id for any dict spec added here.
RECOMMENDED_DEFAULT_SET: tuple[ResourceSpec, ...] = (
    # id deliberately equals the legacy JMdict-XML migration slot
    # (jmdict_importer.JMDICT_DICT_ID) and the default dictionary_chain entry:
    # the download fills exactly the slot a fresh config already points at, and
    # a legacy XML-derived index is upgraded in place by the richer Yomitan
    # build. English JMdict variants (with_examples, without_proper_names)
    # share the same "JMdict [date]" title upstream, so the supersede sweep
    # treats them as the same dictionary — matching Yomitan's own
    # title-keyed semantics.
    ResourceSpec(
        id="jmdict-english",
        kind="dict",
        display_name="JMdict",
        url="https://github.com/yomidevs/jmdict-yomitan/releases/latest/download/JMdict_english.zip",
        license_note="JMdict — © EDRDG, CC BY-SA 4.0, downloaded from upstream source.",
    ),
    ResourceSpec(
        id="jpdb-freq",
        kind="freq",
        display_name="JPDB v2.2 Kana Frequency",
        url=(
            "https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/"
            "JPDB_v2.2_Frequency_Kana_2024-10-13.zip"
        ),
        license_note="JPDB frequency data — downloaded from upstream source; original license applies.",
    ),
    ResourceSpec(
        id="kanjium-pitch",
        kind="pitch",
        display_name="Kanjium Pitch Accent",
        url="https://raw.githubusercontent.com/mifunetoshiro/kanjium/master/data/source_files/raw/accents.txt",
        license_note="Kanjium pitch accent data — downloaded from upstream source; original license applies.",
    ),
)

# Pinned on-disk slots for the recommended dict resources. Consumed by the
# per-row Re-import guard (a catalog slot accepts a newer, same-base zip whose
# title-derived id would otherwise differ) and available for boundary tests.
CATALOG_DICT_SLOT_IDS: frozenset[str] = frozenset(s.id for s in RECOMMENDED_DEFAULT_SET if s.kind == "dict")

# Former catalog dict slots. Users who installed these via an earlier wizard
# keep the pinned-slot Re-import affordance (a newer same-base zip re-imports
# into the stable slot even though its title-derived id differs).
LEGACY_DICT_SLOT_IDS: frozenset[str] = frozenset({"jitendex"})
