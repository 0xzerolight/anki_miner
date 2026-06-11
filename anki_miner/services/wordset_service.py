"""Service for bundled name/proper-noun wordsets (Issue #59)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

logger = logging.getLogger(__name__)

_RESOURCE_PACKAGE = "anki_miner.resources.wordsets"

# Canonical bundled set IDs, in display order. Labels here are fallbacks;
# the file header's "label:" wins when present.
WORDSET_IDS: tuple[str, ...] = ("surnames", "given-names", "place-names", "org-product")
_FALLBACK_LABELS = {
    "surnames": "Surnames",
    "given-names": "Given names",
    "place-names": "Place names",
    "org-product": "Company / Product / Org",
}


@dataclass(frozen=True)
class WordsetInfo:
    """Catalog entry describing one bundled wordset."""

    id: str
    label: str
    count: int


def _resource_root(resource_dir: Path | None) -> Path:
    """Return the directory holding wordset files.

    ``resource_dir`` overrides for tests; otherwise resolve the bundled
    package resource directory (works under pip installs and PyInstaller).
    """
    if resource_dir is not None:
        return resource_dir
    return Path(str(files(_RESOURCE_PACKAGE)))


def _read_header(path: Path) -> dict[str, str]:
    """Read ``# key: value`` header lines until the first data line."""
    meta: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            if not stripped.startswith("#"):
                break
            body = stripped.lstrip("#").strip()
            if ":" in body:
                key, _, value = body.partition(":")
                meta[key.strip().lower()] = value.strip()
    return meta


def load_wordset_catalog(resource_dir: Path | None = None) -> list[WordsetInfo]:
    """List available bundled wordsets with label + entry count.

    Reads only the file header (cheap), not the full word list. Missing
    files are skipped so a partial install degrades gracefully.
    """
    root = _resource_root(resource_dir)
    catalog: list[WordsetInfo] = []
    for set_id in WORDSET_IDS:
        path = root / f"{set_id}.txt"
        if not path.exists():
            continue
        meta = _read_header(path)
        label = meta.get("label", _FALLBACK_LABELS.get(set_id, set_id))
        try:
            count = int(meta.get("count", "0"))
        except ValueError:
            count = 0
        catalog.append(WordsetInfo(id=set_id, label=label, count=count))
    return catalog


class WordsetService:
    """Union of the user-enabled bundled name wordsets.

    I/O-free ``__init__``; disk reads happen in the explicit ``load()``
    (registry pattern, mirrors WordListService / DictionaryRegistry).
    """

    def __init__(self, enabled_ids: tuple[str, ...], resource_dir: Path | None = None):
        self._enabled_ids = tuple(enabled_ids)
        self._resource_dir = resource_dir
        self._blacklist: set[str] = set()
        self._loaded = False

    def load(self) -> None:
        """Read every enabled set into the unioned blacklist."""
        root = _resource_root(self._resource_dir)
        words: set[str] = set()
        for set_id in self._enabled_ids:
            path = root / f"{set_id}.txt"
            if not path.exists():
                logger.warning("Wordset '%s' not found at %s; skipping", set_id, path)
                continue
            words |= self._read_words(path)
        self._blacklist = words
        self._loaded = True
        logger.info("Loaded %d words from %d wordset(s)", len(words), len(self._enabled_ids))

    def is_available(self) -> bool:
        """True once loaded with at least one word."""
        return self._loaded and bool(self._blacklist)

    def is_excluded(self, word: str) -> bool:
        """True if ``word`` is on any enabled wordset."""
        return word in self._blacklist

    @staticmethod
    def _read_words(path: Path) -> set[str]:
        words: set[str] = set()
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    words.add(stripped)
        return words
