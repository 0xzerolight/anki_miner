#!/usr/bin/env python3
"""Translation-payload-loss gate for the i18n catalogs.

Compares every ``*.ts`` catalog in the working tree against a base git ref and
fails if a source string that had a **non-empty translation** at the base has
lost it — the exact failure mode of ``scripts/i18n.py extract`` (pylupdate6
``--no-obsolete``), which DROPS old-context entries and re-creates them EMPTY
in the new context when a ``self.tr()`` string is relocated to a renamed class.

Keying is by SOURCE TEXT ACROSS CONTEXTS on purpose: a correctly-carried
context move (payload physically moved old-context -> new-context) leaves the
per-source multiset unchanged, so it nets to zero and passes. Strict
``(context, source)`` keying would false-fail every legitimate move.

Two failure signals per catalog:
  1. net regression   — a source whose translated-entry count dropped between
                        base and working tree (payload lost, not carried).
  2. new unfinished   — a source that WAS translated at base and now appears in
                        an additional ``type="unfinished"`` entry (a spurious
                        pylupdate stub for a string that was already translated).

Pure stdlib: ``xml.etree`` for parsing, ``subprocess`` (git show) for the base.

Usage:
    python scripts/i18n_payload_check.py [--base <git-ref>]

``--base`` defaults to the merge-base of HEAD with ``main``.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TS_DIR = REPO_ROOT / "anki_miner" / "gui" / "resources" / "translations"


@dataclass
class CatalogPayload:
    """Per-catalog view keyed by source text across all contexts."""

    # source -> number of entries whose <translation> is non-empty.
    translated: Counter[str] = field(default_factory=Counter)
    # source -> number of entries carrying type="unfinished".
    unfinished: Counter[str] = field(default_factory=Counter)


def _translation_is_nonempty(translation: ET.Element | None) -> bool:
    """A translation counts as present if it carries any non-whitespace text.

    Numerus messages count when ANY <numerusform> is non-empty; plain messages
    count when the <translation> element's own text is non-empty.
    """
    if translation is None:
        return False
    forms = translation.findall("numerusform")
    if forms:
        return any((form.text or "").strip() for form in forms)
    return bool((translation.text or "").strip())


def parse_catalog(xml_text: str) -> CatalogPayload:
    """Parse a Qt Linguist ``.ts`` document into a :class:`CatalogPayload`.

    Empty/whitespace input yields an empty payload (a catalog absent at the base
    ref cannot have lost anything).
    """
    payload = CatalogPayload()
    if not xml_text.strip():
        return payload
    root = ET.fromstring(xml_text)
    for context in root.findall("context"):
        for message in context.findall("message"):
            source_el = message.find("source")
            if source_el is None or source_el.text is None:
                continue
            source = source_el.text
            translation = message.find("translation")
            if _translation_is_nonempty(translation):
                payload.translated[source] += 1
            if translation is not None and translation.get("type") == "unfinished":
                payload.unfinished[source] += 1
    return payload


def compare(base: CatalogPayload, work: CatalogPayload) -> tuple[list[tuple[str, int]], list[str]]:
    """Return (lost, newly_unfinished) between a base and working payload.

    ``lost``: sorted (source, count) where the translated-entry count regressed
    (base count exceeds working count).
    ``newly_unfinished``: sorted sources that were translated at base and now
    carry more ``type="unfinished"`` entries than they did at base.
    """
    # Counter subtraction keeps only strictly-positive residuals, i.e. exactly
    # the sources whose translated count dropped.
    regressed = base.translated - work.translated
    lost = sorted(regressed.items())

    newly_unfinished = sorted(
        source
        for source, work_count in work.unfinished.items()
        if base.translated.get(source, 0) > 0 and work_count > base.unfinished.get(source, 0)
    )
    return lost, newly_unfinished


def default_base() -> str:
    """Merge-base of HEAD with ``main``; falls back to the literal ``main``."""
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "merge-base", "HEAD", "main"],
        capture_output=True,
        text=True,
        check=False,
    )
    ref = result.stdout.strip()
    if result.returncode == 0 and ref:
        return ref
    return "main"


def read_base_catalog(base_ref: str, ts_path: Path) -> str:
    """Return ``ts_path``'s contents at ``base_ref`` via ``git show``.

    Returns "" when the file did not exist at that ref (a new catalog).
    """
    rel = ts_path.resolve().relative_to(REPO_ROOT).as_posix()
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "show", f"{base_ref}:{rel}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout


def run_check(
    base_ref: str,
    catalog_paths: Iterable[Path],
    read_base: Callable[[Path], str],
    out: Callable[[str], None] = print,
) -> int:
    """Core gate. Returns 0 when every catalog preserved its payload, else 1.

    ``read_base(ts_path)`` yields the base-ref contents of that catalog (or ""
    if absent), isolating the git dependency for testing.
    """
    ref_label = base_ref[:12]
    any_fail = False
    for ts_path in catalog_paths:
        work = parse_catalog(ts_path.read_text(encoding="utf-8"))
        base = parse_catalog(read_base(ts_path))
        lost, newly_unfinished = compare(base, work)
        name = ts_path.name
        if lost or newly_unfinished:
            any_fail = True
            out(f"FAIL {name}: payload loss vs {ref_label}")
            for source, count in lost:
                out(f"    lost translation (x{count}): {source!r}")
            for source in newly_unfinished:
                out(f"    new unfinished stub for previously-translated: {source!r}")
        else:
            kept = sum(work.translated.values())
            out(f"OK   {name}: {kept} translated entries preserved (base {ref_label})")
    return 1 if any_fail else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail if any i18n catalog lost a non-empty translation vs a base ref.",
    )
    parser.add_argument(
        "--base",
        default=None,
        help="git ref to compare against (default: merge-base of HEAD with main)",
    )
    args = parser.parse_args(argv)
    base_ref = args.base or default_base()

    catalog_paths = sorted(TS_DIR.glob("*.ts"))
    if not catalog_paths:
        print(f"No .ts catalogs found under {TS_DIR}", file=sys.stderr)
        return 1

    return run_check(base_ref, catalog_paths, lambda p: read_base_catalog(base_ref, p))


if __name__ == "__main__":
    raise SystemExit(main())
