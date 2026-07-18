"""Service for bundled name/proper-noun wordsets (Issue #59)."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

logger = logging.getLogger(__name__)

_RESOURCE_PACKAGE = "anki_miner.resources.wordsets"

# Process-wide cache of loaded blacklist unions, keyed by
# (resolved resource dir, frozenset of enabled set IDs). The bundled wordset
# files are immutable at runtime, so a union once read is reusable forever and
# never needs invalidation. Repeated ``WordsetService.load()`` calls (one per
# mining run — the factory rebuilds services every episode) otherwise re-read
# ~480K JMnedict entries into a fresh ~45 MB set each time, ratcheting RSS; the
# cache makes the second and later loads return the SAME frozenset object so the
# big allocation happens once per (dir, id-set). Guarded by a double-checked
# lock, mirroring ``services/tagger.get_shared_tagger``.
_UNION_CACHE: dict[tuple[str, frozenset[str]], frozenset[str]] = {}
_UNION_CACHE_LOCK = threading.Lock()

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


def _read_words(path: Path) -> set[str]:
    """Read one wordset file into a set, skipping blank and ``#`` header lines."""
    words: set[str] = set()
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                words.add(stripped)
    return words


def _load_union_cached(root: Path, enabled_ids: tuple[str, ...]) -> frozenset[str]:
    """Return the unioned blacklist for ``enabled_ids`` under ``root``, cached.

    First call for a given (``root``, id-set) reads each set file and stores the
    frozenset union in the process-wide cache; later calls return that SAME
    object without touching disk. Double-checked locking keeps concurrent first
    loads from doing redundant reads (and from racing the dict write).
    """
    key = (str(root), frozenset(enabled_ids))
    cached = _UNION_CACHE.get(key)
    if cached is not None:
        return cached
    with _UNION_CACHE_LOCK:
        cached = _UNION_CACHE.get(key)
        if cached is not None:
            return cached
        words: set[str] = set()
        for set_id in enabled_ids:
            path = root / f"{set_id}.txt"
            if not path.exists():
                logger.warning("Wordset '%s' not found at %s; skipping", set_id, path)
                continue
            words |= _read_words(path)
        union = frozenset(words)
        _UNION_CACHE[key] = union
        return union


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
        self._blacklist: frozenset[str] = frozenset()
        self._loaded = False

    def load(self) -> None:
        """Read every enabled set into the unioned blacklist.

        Backed by :func:`_load_union_cached`, so the second and later loads for
        the same (resource dir, id-set) reuse one shared frozenset instead of
        re-reading the ~480K-entry files into a fresh set on every mining run.
        """
        root = _resource_root(self._resource_dir)
        self._blacklist = _load_union_cached(root, self._enabled_ids)
        self._loaded = True
        logger.info("Loaded %d words from %d wordset(s)", len(self._blacklist), len(self._enabled_ids))

    def is_available(self) -> bool:
        """True once loaded with at least one word."""
        return self._loaded and bool(self._blacklist)

    def is_excluded(self, word: str) -> bool:
        """True if ``word`` is on any enabled wordset."""
        return word in self._blacklist
