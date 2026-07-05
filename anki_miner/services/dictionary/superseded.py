"""One-time cleanup of date-versioned duplicate dictionaries.

A recommended-resource dict whose ``index.json`` title embeds its release date
(e.g. Jitendex's ``"Jitendex.org [2026-06-06]"``) used to derive a *new* on-disk
``dict_id`` every release, so re-downloading stacked a second directory beside
the old one. The catalog import now pins a stable slot, but pre-fix installs
still have the old date-named directories on disk. :func:`sweep_superseded_dicts`
removes those stale siblings after a catalog dict import.

Identity is deliberately narrow and non-heuristic: an installed dict is a
superseded copy of the just-imported one only when both titles share the *exact*
same base after stripping a trailing ``[YYYY-MM-DD]`` bracket, and both actually
carried such a bracket. This never matches a bracket-less dict or a different
base, so it cannot delete an unrelated or user-curated dictionary.

Pure (no Qt) and structurally total: one unreadable/corrupt/locked sibling
``index.sqlite`` can neither abort the sweep of the other copies nor raise out of
this function — it is logged and skipped.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from anki_miner.services.dictionary.storage import read_meta

logger = logging.getLogger(__name__)

# A trailing " [YYYY-MM-DD]" release-date tag. Required on BOTH sides of a match,
# which is what keeps the base comparison from ever deleting a bracket-less or
# variant-tagged ("[Names]", "[en]") dictionary.
_DATE_BRACKET_RE = re.compile(r"\s*\[\s*\d{4}-\d{2}-\d{2}\s*\]\s*$")


def strip_date_bracket(name: str) -> tuple[str, bool]:
    """Return ``(base, had_bracket)`` for a dictionary title.

    ``base`` is ``name`` with a trailing ``[YYYY-MM-DD]`` tag removed and
    surrounding whitespace trimmed; ``had_bracket`` is True when such a tag was
    present. ``("Jitendex.org [2026-06-06]")`` → ``("Jitendex.org", True)``;
    ``("Daijirin")`` → ``("Daijirin", False)``.
    """
    stripped = name.strip()
    base = _DATE_BRACKET_RE.sub("", stripped).strip()
    return base, base != stripped


def sweep_superseded_dicts(
    dicts_root: Path,
    *,
    keep_id: str,
    imported_source_name: str,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Remove date-versioned duplicates of a just-imported catalog dict.

    Args:
        dicts_root: The dictionaries root (``config.dicts_root``).
        keep_id: The on-disk slot of the freshly-imported dict; never removed.
        imported_source_name: The freshly-imported dict's title. The sweep is a
            no-op unless this itself carries a ``[YYYY-MM-DD]`` tag (so a dict
            that was never date-versioned can't trigger deletions).

    Returns:
        ``(swept, failed)`` — each a list of ``(dict_id, source_name)``. ``swept``
        were removed from disk; ``failed`` matched but could not be removed
        (e.g. Windows file lock) and are surfaced to the user, their chain
        entries left intact so no orphan is created.
    """
    base, had = strip_date_bracket(imported_source_name)
    if not had:
        return [], []

    try:
        children = sorted(dicts_root.iterdir())
    except OSError:
        return [], []

    swept: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []
    for child in children:
        # Guard the whole read: read_meta opens sqlite and can raise
        # sqlite3.Error (NOT OSError) on a corrupt / old-schema / locked sibling.
        # One bad sibling must never abort the loop or fail the import.
        try:
            if not child.is_dir() or child.name == keep_id:
                continue
            db = child / "index.sqlite"
            if not db.exists():
                continue
            cand_name = read_meta(db).get("source_name", "")
        except Exception as e:  # noqa: BLE001 — skip an unreadable sibling, keep sweeping
            logger.warning("skipping unreadable dict %s during supersede sweep: %s", child.name, e)
            continue

        cand_base, cand_had = strip_date_bracket(cand_name)
        if cand_had and cand_base == base:
            try:
                shutil.rmtree(child)
                swept.append((child.name, cand_name))
            except OSError as e:
                logger.warning("could not remove superseded dict %s: %s", child.name, e)
                failed.append((child.name, cand_name))
    return swept, failed
