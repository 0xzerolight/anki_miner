#!/usr/bin/env python
"""Junk-reduction replay harness — dump every mined card front for a subtitle dir.

Dev tool. NEVER imported by the app (it lives in ``scripts/``, outside the
``anki_miner`` package). Sibling of ``scripts/parse_benchmark.py``: same repo-root
``sys.path`` shim, same "drive the REAL parser so the harness can't diverge from
production mining" discipline.

What it does
------------
For every subtitle file in a directory (sorted), it loads per-cue units through
the READING loader (``services/reading/subtitle_source.load`` — the same cue
granularity the video pipeline mines, and explicitly NOT
``parse_subtitle_file``), runs them through a production
``SubtitleParserService`` wired (read-only) to the same three offline probes
``gui/utils/service_factory`` injects, but over EVERY installed
``~/.anki_miner`` dictionary rather than the user's configured chain (the replay
wants maximum attestation; the GUI chain is user-config, not relevant to
parser-behavior diffing — see ``build_parser``), and writes one TSV row per
emitted card front: ``file  mined_form  lemma  reading  sentence``.

Usage
-----
    .venv/bin/python scripts/junk_replay.py <dir-of-subtitle-files> --out replay.tsv

Gate procedure (junk-reduction merge gate)
------------------------------------------
This harness is the defense-in-depth replay behind the junk-reduction units. The
durable proof of each unit is its automated unit tests; the replay is a
reviewer-curated corpus diff run AFTER each unit, never a committed assertion.

Run it twice over the same corpus — once on ``main``, once on the unit branch —
then take the SYMMETRIC diff of the mined-front sets:

  * added fronts (branch − main): every one MUST be in the U11 21-verb expected
    remap set. Remaps ADD fronts (e.g. an attested passive/causative collapse),
    so a hard ``added == ∅`` would false-fail U11 — the check is
    ``added ⊆ expected-remap-set``, not emptiness.
  * removed fronts (main − branch): every one MUST fall in an intended junk
    class — U8(a) 連用形 removals and U8(b) single-char/katakana removals
    classified SEPARATELY (each confirmed junk against the 785-card audit),
    deinflection-arch sources, and kana misrecoveries.
  * reading diff on KEPT fronts (present in both): readings MUST be byte-identical
    — a changed reading on an unchanged front is a silent regression.
  * annotation over-strip tripwire: eyeball a sample of the ~291 sentences that
    carry annotations (the new ``subtitle_cleanup`` input class) for content the
    strip ate.

Corpus: ``/home/light/Downloads/sololeveling s2 subs2/``.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import sys
from pathlib import Path
from typing import NamedTuple

# The harness reaches into the app package for the REAL parser + reading loader.
# scripts/ is not on sys.path when run as ``python scripts/junk_replay.py``, so
# add the repo root before importing anki_miner.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from anki_miner.config.config import AnkiMinerConfig, ChainEntry  # noqa: E402
from anki_miner.models.reading import ReadingSourceRef  # noqa: E402
from anki_miner.services.definition_service import DefinitionService  # noqa: E402
from anki_miner.services.dictionary.registry import DictionaryRegistry  # noqa: E402
from anki_miner.services.reading import subtitle_source  # noqa: E402
from anki_miner.services.subtitle_parser import SubtitleParserService  # noqa: E402

# Suffixes the reading subtitle loader accepts (see subtitle_source docstring).
SUBTITLE_SUFFIXES = frozenset({".srt", ".ass", ".ssa", ".vtt"})

_TSV_COLUMNS = ("file", "mined_form", "lemma", "reading", "sentence")


class ReplayRow(NamedTuple):
    """One emitted card front, tagged with its source file."""

    file: str
    mined_form: str
    lemma: str
    reading: str
    sentence: str


def _all_installed_dicts_config(config: AnkiMinerConfig, registry: DictionaryRegistry) -> AnkiMinerConfig:
    """Return ``config`` with its ``dictionary_chain`` replaced by EVERY installed dict.

    The replay wants *maximum attestation* — the parser's kana-recovery and
    reading probes should see every dictionary on disk so dict-dependent guards
    are visible to the gate. The GUI's ``config.dictionary_chain`` is a
    user-config artifact (which dicts are enabled, in what order) and is NOT
    relevant to diffing parser BEHAVIOR, so it is bypassed here.

    ``registry.unlisted`` against an empty chain returns every on-disk dict with
    ``schema_ok=True`` (schema-stale indexes are dropped, as they would be by
    ``build_provider_chain`` anyway), sorted by dict_id for determinism. Read-only.
    Requires ``registry.load()`` already called.
    """
    empty = dataclasses.replace(config, dictionary_chain=())
    entries = tuple(ChainEntry(kind="indexed", dict_id=meta.dict_id, enabled=True) for meta in registry.unlisted(empty))
    return dataclasses.replace(config, dictionary_chain=entries)


def build_parser(config: AnkiMinerConfig) -> SubtitleParserService:
    """Build the production parser wired to ``config.dicts_root`` (read-only).

    Mirrors ``gui/utils/service_factory``: scan the registry, assemble the
    provider chain, wrap it in a ``DefinitionService``, and inject the SAME four
    probes production wires — ``offline_terms_exist`` as ``term_lookup`` (drives
    ``resolve_dictionary_form``), ``offline_term_readings`` as ``reading_lookup``
    (drives the merged-compound reading attestation, audit F2),
    ``has_offline_definitions`` as ``kana_attest_lookup`` (drives the WS2 kana
    recovery), and ``offline_term_commonness`` as ``term_common_lookup`` (drives
    the U11 verb-front resolver, narrowing the deinflection override pool to
    headwords a commonness-aware dict tags common). All four probes lazily load
    the chain, so no explicit ``ensure_loaded`` is needed; with an empty
    ``dicts_root`` they attest nothing (the offline-free replay path).

    Unlike the GUI, the chain is assembled from ALL installed dictionaries
    (``_all_installed_dicts_config``), not ``config.dictionary_chain`` — the
    replay wants maximum attestation and the user's chain config is irrelevant to
    diffing parser behavior. See that helper for the rationale.
    """
    registry = DictionaryRegistry(config.dicts_root)
    registry.load()
    replay_config = _all_installed_dicts_config(config, registry)
    definition_service = DefinitionService(replay_config, providers=registry.build_provider_chain(replay_config))
    return SubtitleParserService(
        replay_config,
        term_lookup=definition_service.offline_terms_exist,
        reading_lookup=definition_service.offline_term_readings,
        kana_attest_lookup=definition_service.has_offline_definitions,
        term_common_lookup=definition_service.offline_term_commonness,
    )


def replay_file(parser: SubtitleParserService, path: Path) -> list[ReplayRow]:
    """Mine one subtitle file into its emitted card-front rows.

    Loads per-cue units via the reading loader, then runs the real
    ``parse_text_units`` with ``subtitle_cleanup=True`` (the video-path
    annotation-strip seam) so the output equals what production mining emits.
    """
    document = subtitle_source.load(ReadingSourceRef(kind="subtitle", path=path))
    words, _index, _counts = parser.parse_text_units(
        document.units,
        want_line_index=False,
        subtitle_cleanup=True,
    )
    return [
        ReplayRow(
            file=path.name,
            mined_form=w.mined_form,
            lemma=w.lemma,
            reading=w.reading,
            sentence=w.sentence,
        )
        for w in words
    ]


def replay_dir(directory: Path, parser: SubtitleParserService) -> list[ReplayRow]:
    """Mine every subtitle file under ``directory`` (sorted), concatenating rows."""
    rows: list[ReplayRow] = []
    for path in sorted(directory.iterdir()):
        if path.is_file() and path.suffix.lower() in SUBTITLE_SUFFIXES:
            rows.extend(replay_file(parser, path))
    return rows


def write_tsv(path: Path, rows: list[ReplayRow]) -> None:
    """Write replay rows to a tab-separated file with a header row."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(_TSV_COLUMNS)
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code (0 = success)."""
    parser = argparse.ArgumentParser(description="Replay a subtitle directory through the real parser to TSV.")
    parser.add_argument("directory", type=Path, help="Directory of subtitle files (.srt/.ass/.ssa/.vtt).")
    parser.add_argument("--out", type=Path, required=True, help="Output TSV path.")
    args = parser.parse_args(argv)

    if not args.directory.is_dir():
        print(f"Not a directory: {args.directory}", file=sys.stderr)
        return 1

    service = build_parser(AnkiMinerConfig())
    rows = replay_dir(args.directory, service)
    write_tsv(args.out, rows)
    print(f"Wrote {len(rows)} rows from {args.directory} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
