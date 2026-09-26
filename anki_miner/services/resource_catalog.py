"""Recommended downloadable resources — single source of truth.

Pure data, no I/O. RESOURCES.md lists these URLs, and every language
catalog's, under that language's heading; tests/unit/test_support_docs.py
fails when one is missing. A later Qt worker iterates
:data:`RECOMMENDED_DEFAULT_SET`, downloads each ``url`` via
``resource_downloader.download_to_temp``, then routes the temp file to the
right importer based on ``kind`` (``dict`` → Yomitan importer, ``freq`` →
per-source frequency importer, ``pitch`` → per-source pitch importer).
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
        lemmatise: Import a word-count list as occurrence counts aggregated
            per lemma with the language's tagger (S17).
        variant: The ``config.script_variant`` id this resource belongs to
            ("" = every variety). The setup wizard starts a spec ticked only when
            its variant is "" or the config's own (pt's two frequency lists).
        pin_slot: Freq only. Import into ``freqs_root/<id>/`` instead of the
            slot derived from the zip title. For a list served from a moving
            URL: each build carries a new ``index.json`` revision, and a
            title-derived slot would fork ``<slug>-<hash>`` beside the old one,
            leaving two enabled chain entries for one list. Dict and pitch
            specs always import into ``<id>``.
    """

    id: str
    kind: str
    display_name: str
    url: str
    license_note: str
    lemmatise: bool = False
    variant: str = ""
    pin_slot: bool = False


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
    # Jiten serves its list from an always-latest API endpoint, and every build
    # carries a new index.json revision ("Jiten 26-09-21"): pin_slot keeps each
    # re-download in one slot. The id is the slug of the zip title "Jiten", so a
    # hand-imported copy is the slot the download replaces. Placed after JPDB so
    # it lands first in frequency_chain (apply_download_summary prepends each
    # success in catalog order), putting Jiten first on the card.
    ResourceSpec(
        id="jiten",
        kind="freq",
        display_name="Jiten Frequency",
        url="https://api.jiten.moe/api/frequency-list/download?downloadType=yomitan",
        license_note="Jiten frequency data (jiten.moe) — CC BY-SA 4.0, downloaded from upstream source.",
        pin_slot=True,
    ),
    ResourceSpec(
        id="kanjium-pitch",
        kind="pitch",
        display_name="Kanjium Pitch Accent",
        url="https://raw.githubusercontent.com/mifunetoshiro/kanjium/master/data/source_files/raw/accents.txt",
        license_note="Kanjium pitch accent data — downloaded from upstream source; original license applies.",
    ),
)

# Pinned on-disk slots for this catalog's dict resources. The per-row Re-import
# guard derives its own set from the *active* profile's catalog, so this is the
# boundary-test anchor for the Japanese one: every dict spec above must be in it.
CATALOG_DICT_SLOT_IDS: frozenset[str] = frozenset(s.id for s in RECOMMENDED_DEFAULT_SET if s.kind == "dict")

# Former catalog dict slots. Users who installed these via an earlier wizard
# keep the pinned-slot Re-import affordance (a newer same-base zip re-imports
# into the stable slot even though its title-derived id differs).
LEGACY_DICT_SLOT_IDS: frozenset[str] = frozenset({"jitendex"})
