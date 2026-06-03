"""Generate bundled name wordset files from JMnedict (proper names) and JMdict_e.

Sources:
  JMnedict — Japanese Proper Names Dictionary, maintained by EDRDG.
    Download: http://ftp.edrdg.org/pub/Nihongo/JMnedict.xml.gz
    License: Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0)
    See: https://www.edrdg.org/enamdict/enamdict_doc.html

  JMdict_e — Japanese-English Dictionary (English-only sense file), maintained by EDRDG.
    Download: http://ftp.edrdg.org/pub/Nihongo/JMdict_e.gz
    Used only to identify common words that should remain mineable (overlap-drop).

Usage (defaults output to anki_miner/gui/resources/wordsets/):
    python3 scripts/build_name_wordsets.py \\
        --jmnedict /tmp/JMnedict.xml \\
        --jmdict   /tmp/JMdict_e.xml

lxml is required (dev-only, not a runtime dependency):
    pip install lxml
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from lxml import etree

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# JMnedict name_type text → bucket id.
#
# lxml with load_dtd=True + resolve_entities=True expands entities to their
# full descriptive strings as defined in the in-file DTD, e.g.:
#   &surname;   → "family or surname"
#   &place;     → "place name"
#   &given;     → "given name or forename, gender not specified"
#   &masc;      → "male given name or forename"
#   &fem;       → "female given name or forename"
#   &person;    → "full name of a particular person"  (folds into given-names)
#   &company;   → "company name"
#   &product;   → "product name"
#   &organization; → "organization name"
#   &work;      → "work of art, literature, music, etc. name"
#
# Other JMnedict name types (e.g. "unclassified name", "railway station",
# "character", "deity", "event", "group", "mythology", "ship name", ...) are
# intentionally NOT mapped and are dropped: they fall outside the four buckets
# this feature ships. The largest are "unclassified name" (~130k, genuinely
# unclassifiable) and "railway station" (~8k). If a future maintainer wants
# stations excluded as places, add "railway station": "place-names" here.
TYPE_TO_BUCKET: dict[str, str] = {
    "family or surname": "surnames",
    "place name": "place-names",
    "given name or forename, gender not specified": "given-names",
    "male given name or forename": "given-names",
    "female given name or forename": "given-names",
    "full name of a particular person": "given-names",
    "company name": "org-product",
    "product name": "org-product",
    "organization name": "org-product",
    "work of art, literature, music, etc. name": "org-product",
}

# Ordered bucket ids (matches WORDSET_IDS in wordset_service.py)
BUCKET_IDS: tuple[str, ...] = ("surnames", "given-names", "place-names", "org-product")

# Human-readable labels (matches _FALLBACK_LABELS in wordset_service.py)
BUCKET_LABELS: dict[str, str] = {
    "surnames": "Surnames",
    "given-names": "Given names",
    "place-names": "Place names",
    "org-product": "Company / Product / Org",
}

# JMdict ke_pri values that indicate a common everyday word
COMMON_PRIORITY_TAGS: frozenset[str] = frozenset({"news1", "ichi1", "spec1", "gai1"})

SOURCE_STRING = "JMnedict (EDRDG) — CC BY-SA 4.0"

FILE_HEADER_TEMPLATE = """\
# Anki Miner name wordset
# id: {bucket_id}
# label: {label}
# source: {source}
# count: {count}
"""

# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _lxml_parser() -> etree.XMLParser:
    """Return an lxml parser that loads the inline DTD and expands entities."""
    return etree.XMLParser(
        load_dtd=True,
        resolve_entities=True,
        no_network=True,
        huge_tree=True,
    )


def _build_common_words(jmdict: Path) -> frozenset[str]:
    """Return the set of JMdict headwords (keb) that carry a common priority tag.

    Only ``<keb>`` elements whose sibling ``<ke_pri>`` text is in
    ``COMMON_PRIORITY_TAGS`` (news1, ichi1, spec1, gai1) are included.
    """
    logger.info("Scanning JMdict for common words: %s", jmdict)
    # JMdict_e uses standard XML with no entity references, so stdlib ET would
    # work, but we use lxml for consistency and to handle any edge cases.
    tree = etree.parse(str(jmdict), _lxml_parser())
    common: set[str] = set()
    for k_ele in tree.iterfind(".//k_ele"):
        keb_el = k_ele.find("keb")
        if keb_el is None or not keb_el.text:
            continue
        priorities = {el.text for el in k_ele.findall("ke_pri") if el.text}
        if priorities & COMMON_PRIORITY_TAGS:
            common.add(keb_el.text)
    logger.info("Found %d common JMdict headwords (overlap-drop set)", len(common))
    return frozenset(common)


def _bucket_names(jmnedict: Path, common_words: frozenset[str]) -> dict[str, set[str]]:
    """Parse JMnedict and distribute keb values into buckets.

    Each ``<entry>`` may have multiple ``<k_ele>`` (writing variants) and
    multiple ``<trans>`` blocks (each with one ``<name_type>``).  Every keb
    that is NOT in ``common_words`` is added to the appropriate bucket for
    each of its name types.  Unknown name_type values are silently skipped.
    """
    logger.info("Parsing JMnedict: %s", jmnedict)
    parser = _lxml_parser()
    tree = etree.parse(str(jmnedict), parser)

    buckets: dict[str, set[str]] = {bid: set() for bid in BUCKET_IDS}
    skipped_common = 0
    skipped_unknown_type = 0

    for entry in tree.iterfind(".//entry"):
        # Collect all keb values for this entry
        kebs = [el.text for el in entry.findall(".//k_ele/keb") if el.text]
        if not kebs:
            continue

        # Collect all name_type values for this entry
        name_types = [el.text for el in entry.findall(".//trans/name_type") if el.text]
        if not name_types:
            continue

        for keb in kebs:
            if keb in common_words:
                skipped_common += 1
                continue
            for name_type in name_types:
                bucket = TYPE_TO_BUCKET.get(name_type)
                if bucket is None:
                    skipped_unknown_type += 1
                    continue
                buckets[bucket].add(keb)

    logger.info(
        "Bucketing complete. Skipped %d common-word overlaps, %d unknown name types.",
        skipped_common,
        skipped_unknown_type,
    )
    return buckets


def _write_wordset(out_dir: Path, bucket_id: str, words: set[str]) -> int:
    """Write a sorted, deduped wordset file with a metadata header.

    Returns the count of words written.
    """
    path = out_dir / f"{bucket_id}.txt"
    label = BUCKET_LABELS[bucket_id]
    sorted_words = sorted(words)
    count = len(sorted_words)
    header = FILE_HEADER_TEMPLATE.format(
        bucket_id=bucket_id,
        label=label,
        source=SOURCE_STRING,
        count=count,
    )
    with path.open("w", encoding="utf-8") as f:
        f.write(header)
        for word in sorted_words:
            f.write(word + "\n")
    logger.info("Wrote %d words to %s", count, path)
    return count


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_wordsets(
    jmnedict: Path,
    jmdict: Path,
    out_dir: Path,
) -> dict[str, int]:
    """Build and write all four wordset files.

    Args:
        jmnedict: Path to JMnedict.xml (uncompressed).
        jmdict:   Path to JMdict_e.xml (uncompressed), used for overlap-drop.
        out_dir:  Directory to write ``<bucket-id>.txt`` files into (created if missing).

    Returns:
        Mapping of bucket_id → word count for each written file.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    common_words = _build_common_words(jmdict)
    buckets = _bucket_names(jmnedict, common_words)
    counts: dict[str, int] = {}
    for bucket_id in BUCKET_IDS:
        counts[bucket_id] = _write_wordset(out_dir, bucket_id, buckets[bucket_id])
    return counts


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

_DEFAULT_OUT_DIR = Path(__file__).parent.parent / "anki_miner" / "gui" / "resources" / "wordsets"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Generate bundled name wordset files from JMnedict + JMdict_e.",
        epilog=(
            "Downloads:\n"
            "  JMnedict: curl -L http://ftp.edrdg.org/pub/Nihongo/JMnedict.xml.gz | gunzip > /tmp/JMnedict.xml\n"
            "  JMdict_e: curl -L http://ftp.edrdg.org/pub/Nihongo/JMdict_e.gz | gunzip > /tmp/JMdict_e.xml"
        ),
    )
    parser.add_argument(
        "--jmnedict",
        type=Path,
        required=True,
        metavar="PATH",
        help="Path to uncompressed JMnedict.xml",
    )
    parser.add_argument(
        "--jmdict",
        type=Path,
        required=True,
        metavar="PATH",
        help="Path to uncompressed JMdict_e.xml (for common-word overlap drop)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=_DEFAULT_OUT_DIR,
        metavar="DIR",
        help=f"Output directory (default: {_DEFAULT_OUT_DIR})",
    )
    args = parser.parse_args()
    counts = build_wordsets(jmnedict=args.jmnedict, jmdict=args.jmdict, out_dir=args.out_dir)
    for bucket_id in BUCKET_IDS:
        print(f"{bucket_id}: {counts[bucket_id]:,} words → {args.out_dir / (bucket_id + '.txt')}")


if __name__ == "__main__":
    main()
