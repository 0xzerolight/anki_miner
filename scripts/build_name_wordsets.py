"""Generate bundled name wordset files from JMnedict (proper names) and JMdict_e.

Sources:
  JMnedict — Japanese Proper Names Dictionary, maintained by EDRDG.
    Download: http://ftp.edrdg.org/pub/Nihongo/JMnedict.xml.gz
    License: Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0)
    See: https://www.edrdg.org/enamdict/enamdict_doc.html

  JMdict_e — Japanese-English Dictionary (English-only sense file), maintained by EDRDG.
    Download: http://ftp.edrdg.org/pub/Nihongo/JMdict_e.gz
    Used only to identify common words that should remain mineable (overlap-drop).

  Jiten frequency list (jiten.moe) — Yomitan zip, CC BY-SA 4.0.
    Download: https://api.jiten.moe/api/frequency-list/download?downloadType=yomitan
    Used only to pick everyday words out of the name lists; no rank is written.

Usage (defaults output to anki_miner/resources/wordsets/):
    python3 scripts/build_name_wordsets.py \\
        --jmnedict /tmp/JMnedict.xml \\
        --jmdict   /tmp/JMdict_e.xml \\
        --freq     /tmp/jiten.zip

lxml is required (dev-only, not a runtime dependency):
    pip install lxml
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import zipfile
from pathlib import Path
from typing import Callable

from lxml import etree

from anki_miner.services.frequency.csv_parse import is_kana_usage_display, normalize_freq_rank

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

# JMdict words this high on Jiten's list are everyday words even when JMnedict
# also lists them as names (魔王 7,820, 安堵 4,101, 部室 10,218). Single
# characters stay listed: they are the commonest given names (凛, 栞) and also
# pieces of longer words (屋, 代, 解 in 卍解) that the lists keep off cards.
EVERYDAY_RANK_CUTOFF = 20_000

# A JMdict word glued to one of these particles (夢の, 上の, 秋を) is not a name;
# listing it lets the name matcher swallow 夢 into "夢の".
PARTICLE_KANA: frozenset[str] = frozenset("のをがにはとでも")

# CJK ideographs (Ext A, URO, compatibility, Ext B+) plus 々 and 〆.
_ALL_KANJI_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿\U00020000-\U0003134f々〆]+")

SOURCE_STRING = "JMnedict (EDRDG) — CC BY-SA 4.0"

FILTER_STRING = (
    f"minus JMdict common words, JMdict words in Jiten's top {EVERYDAY_RANK_CUTOFF} "
    "(2+ characters), and JMdict word + particle (夢の)"
)

FILE_HEADER_TEMPLATE = """\
# Anki Miner name wordset
# id: {bucket_id}
# label: {label}
# source: {source}
# filter: {filter}
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


def _scan_jmdict(jmdict: Path) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(common, headwords)``: kebs with a common priority tag, and every keb.

    Common means a sibling ``<ke_pri>`` in ``COMMON_PRIORITY_TAGS`` (news1,
    ichi1, spec1, gai1).
    """
    logger.info("Scanning JMdict: %s", jmdict)
    # JMdict_e uses standard XML with no entity references, so stdlib ET would
    # work, but we use lxml for consistency and to handle any edge cases.
    tree = etree.parse(str(jmdict), _lxml_parser())
    common: set[str] = set()
    headwords: set[str] = set()
    for k_ele in tree.iterfind(".//k_ele"):
        keb_el = k_ele.find("keb")
        if keb_el is None or not keb_el.text:
            continue
        headwords.add(keb_el.text)
        priorities = {el.text for el in k_ele.findall("ke_pri") if el.text}
        if priorities & COMMON_PRIORITY_TAGS:
            common.add(keb_el.text)
    logger.info("Found %d JMdict headwords, %d common", len(headwords), len(common))
    return frozenset(common), frozenset(headwords)


def _load_frequency_ranks(freq_zip: Path) -> dict[str, int]:
    """Best rank per term in a rank-based Yomitan frequency zip; ㋕ kana-usage rows skipped."""
    ranks: dict[str, int] = {}
    with zipfile.ZipFile(freq_zip) as archive:
        mode = json.loads(archive.read("index.json")).get("frequencyMode")
        if mode != "rank-based":
            raise ValueError(f"{freq_zip}: expected a rank-based frequency list, got {mode!r}")
        for name in archive.namelist():
            if not (name.startswith("term_meta_bank_") and name.endswith(".json")):
                continue
            for row in json.loads(archive.read(name)):
                if len(row) < 3 or row[1] != "freq":
                    continue
                rank, display = normalize_freq_rank(row[2])
                if rank is None or is_kana_usage_display(display):
                    continue
                if rank < ranks.get(row[0], rank + 1):
                    ranks[row[0]] = rank
    logger.info("Loaded %d ranked terms from %s", len(ranks), freq_zip)
    return ranks


def _drop_reason(keb: str, headwords: frozenset[str], ranks: dict[str, int]) -> str | None:
    """Why a JMnedict spelling is an everyday word rather than a name; None keeps it."""
    if len(keb) < 2:
        return None
    if keb in headwords and ranks.get(keb, EVERYDAY_RANK_CUTOFF + 1) <= EVERYDAY_RANK_CUTOFF:
        return "everyday"
    prefix = keb[:-1]
    if keb[-1] in PARTICLE_KANA and _ALL_KANJI_RE.fullmatch(prefix) and prefix in headwords:
        return "particle"
    return None


def _bucket_names(
    jmnedict: Path, common_words: frozenset[str], drop_reason: Callable[[str], str | None]
) -> tuple[dict[str, set[str]], dict[str, str]]:
    """Parse JMnedict and distribute keb values into buckets.

    Each ``<entry>`` may have multiple ``<k_ele>`` (writing variants) and
    multiple ``<trans>`` blocks (each with one ``<name_type>``).  Every keb
    that is NOT in ``common_words`` and has no ``drop_reason`` is added to the
    appropriate bucket for each of its name types.  Unknown name_type values
    are silently skipped.  Returns the buckets and ``{keb: reason}`` for every
    spelling ``drop_reason`` removed.
    """
    logger.info("Parsing JMnedict: %s", jmnedict)
    parser = _lxml_parser()
    tree = etree.parse(str(jmnedict), parser)

    buckets: dict[str, set[str]] = {bid: set() for bid in BUCKET_IDS}
    dropped: dict[str, str] = {}
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
            reason = drop_reason(keb)
            if reason is not None:
                dropped[keb] = reason
                continue
            for name_type in name_types:
                bucket = TYPE_TO_BUCKET.get(name_type)
                if bucket is None:
                    skipped_unknown_type += 1
                    continue
                buckets[bucket].add(keb)

    logger.info(
        "Bucketing complete. Skipped %d common-word overlaps, %d everyday/particle spellings, %d unknown name types.",
        skipped_common,
        len(dropped),
        skipped_unknown_type,
    )
    return buckets, dropped


def _write_report(path: Path, dropped: dict[str, str], ranks: dict[str, int]) -> None:
    """One ``spelling<TAB>reason<TAB>rank`` line per dropped spelling, for review."""
    rows = sorted(dropped.items(), key=lambda item: (item[1], ranks.get(item[0], 0), item[0]))
    path.write_text("".join(f"{keb}\t{reason}\t{ranks.get(keb, '')}\n" for keb, reason in rows), encoding="utf-8")
    logger.info("Wrote %d dropped spellings to %s", len(rows), path)


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
        filter=FILTER_STRING,
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
    freq: Path,
    out_dir: Path,
    report: Path | None = None,
) -> dict[str, int]:
    """Build and write all four wordset files.

    Args:
        jmnedict: Path to JMnedict.xml (uncompressed).
        jmdict:   Path to JMdict_e.xml (uncompressed): common-word and headword sets.
        freq:     Path to a rank-based Yomitan frequency zip (Jiten): everyday-word ranks.
        out_dir:  Directory to write ``<bucket-id>.txt`` files into (created if missing).
        report:   Optional TSV listing every spelling the drop rules removed.

    Returns:
        Mapping of bucket_id → word count for each written file.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    common_words, headwords = _scan_jmdict(jmdict)
    ranks = _load_frequency_ranks(freq)
    buckets, dropped = _bucket_names(jmnedict, common_words, lambda keb: _drop_reason(keb, headwords, ranks))
    if report is not None:
        _write_report(report, dropped, ranks)
    counts: dict[str, int] = {}
    for bucket_id in BUCKET_IDS:
        counts[bucket_id] = _write_wordset(out_dir, bucket_id, buckets[bucket_id])
    return counts


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

_DEFAULT_OUT_DIR = Path(__file__).parent.parent / "anki_miner" / "resources" / "wordsets"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Generate bundled name wordset files from JMnedict + JMdict_e.",
        epilog=(
            "Downloads:\n"
            "  JMnedict: curl -L http://ftp.edrdg.org/pub/Nihongo/JMnedict.xml.gz | gunzip > /tmp/JMnedict.xml\n"
            "  JMdict_e: curl -L http://ftp.edrdg.org/pub/Nihongo/JMdict_e.gz | gunzip > /tmp/JMdict_e.xml\n"
            "  Jiten:    curl -L -o /tmp/jiten.zip "
            "'https://api.jiten.moe/api/frequency-list/download?downloadType=yomitan'"
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
        "--freq",
        type=Path,
        required=True,
        metavar="PATH",
        help="Path to the Jiten Yomitan frequency zip (everyday-word ranks)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        metavar="PATH",
        help="Write dropped spellings (TSV) here for review",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=_DEFAULT_OUT_DIR,
        metavar="DIR",
        help=f"Output directory (default: {_DEFAULT_OUT_DIR})",
    )
    args = parser.parse_args()
    counts = build_wordsets(
        jmnedict=args.jmnedict,
        jmdict=args.jmdict,
        freq=args.freq,
        out_dir=args.out_dir,
        report=args.report,
    )
    for bucket_id in BUCKET_IDS:
        print(f"{bucket_id}: {counts[bucket_id]:,} words → {args.out_dir / (bucket_id + '.txt')}")


if __name__ == "__main__":
    main()
