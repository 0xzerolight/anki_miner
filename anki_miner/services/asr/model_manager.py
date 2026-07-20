"""ASR model management — download and presence checks.

Stateless functions that take ``models_root`` explicitly so they have no
coupling to the global config. Callers are responsible for passing
``config.asr_models_root``.
"""

import logging
import os
import shutil
import tempfile
from pathlib import Path

from anki_miner.services.asr import _engine
from anki_miner.utils.atomic_io import atomic_replace_dir, reconcile_backups_in

logger = logging.getLogger(__name__)

KNOWN_MODELS: frozenset[str] = frozenset({"large-v3", "small"})
DEFAULT_MODEL: str = "large-v3"

#: Files faster-whisper / ctranslate2 require alongside ``model.bin`` for a
#: model to actually load. We require at least ``config.json`` so a download
#: interrupted before the metadata landed is not mistaken for a complete model.
_REQUIRED_SIBLING = "config.json"


def _name_matches(part: str, name: str) -> bool:
    """Return True if directory-name *part* corresponds to model *name*.

    Anchored to the faster-whisper HF-cache convention
    ``models--<org>--faster-whisper-<name>`` (so ``large`` does not match a
    ``large-v3`` directory), while still accepting a flat
    ``faster-whisper-<name>`` or bare ``<name>`` layout.
    """
    return part == name or part.endswith(f"faster-whisper-{name}")


def is_downloaded(name: str, models_root: Path) -> bool:
    """Return True if the model *name* is present **and complete** in *models_root*.

    Presence of ``model.bin`` alone is not sufficient: an interrupted download
    can leave a truncated ``model.bin`` (or one without its metadata) that would
    then fail at load time with an opaque ctranslate2 error. We therefore require:

    1. a ``model.bin`` whose containing path has an ancestor directory matching
       *name* (see :func:`_name_matches`, anchored to the faster-whisper layout);
    2. a non-empty ``model.bin`` (resolving symlinks — HF stores the real bytes
       in a ``blobs/`` blob the snapshot links to);
    3. the required ``config.json`` sibling in the same directory.

    Note that :func:`download` promotes models into *models_root* atomically, so
    in normal operation *models_root* never contains a partial model; this check
    is defense-in-depth against externally corrupted or pre-existing caches.

    Args:
        name: Model identifier (must be in ``KNOWN_MODELS``).
        models_root: Directory that contains downloaded model subdirectories;
            typically ``config.asr_models_root``.

    Returns:
        ``True`` if a complete model is present, ``False`` otherwise.
    """
    reconcile_backups_in(models_root)
    if not models_root.exists():
        return False
    for candidate in models_root.rglob("model.bin"):
        if candidate.parent == models_root:
            # model.bin sitting directly in models_root itself — not a valid layout
            continue
        rel_parts = candidate.relative_to(models_root).parts
        if not any(_name_matches(part, name) for part in rel_parts):
            continue
        # Integrity: non-empty payload + required metadata sibling present.
        try:
            if candidate.stat().st_size == 0:
                continue
        except OSError:
            # Dangling symlink (incomplete download) — treat as not present.
            continue
        if not (candidate.parent / _REQUIRED_SIBLING).is_file():
            continue
        return True
    return False


def download(name: str, models_root: Path, cancel_event=None) -> None:
    """Download model *name* into *models_root* atomically.

    The model is fetched into a private staging directory *inside* ``models_root``
    (same filesystem, so promotion is atomic) and only moved into place on
    success. A failure, exception, or a ``cancel_event`` set mid-download leaves
    ``models_root`` untouched — no partial model is ever visible to
    :func:`is_downloaded`.

    Args:
        name: Model identifier (must be in ``KNOWN_MODELS``).
        models_root: Target directory; created if it does not exist.
        cancel_event: Optional ``threading.Event``. Checked before the download
            starts and after it returns; the underlying HF download itself is a
            single blocking call and cannot be interrupted mid-transfer, so a
            mid-download cancel takes effect once the transfer finishes — at
            which point nothing is promoted.
    """
    if cancel_event is not None and cancel_event.is_set():
        return
    models_root.mkdir(parents=True, exist_ok=True)

    staging = Path(tempfile.mkdtemp(prefix=f".staging-{name}-", dir=models_root))
    try:
        # cache_dir (NOT download_root): faster-whisper's download_model has no
        # download_root param — that belongs to WhisperModel. cache_dir lays out
        # the HF cache tree (models--Systran--faster-whisper-<name>/snapshots/…)
        # that both is_downloaded and WhisperModel(download_root=…,
        # local_files_only=True) expect. output_dir would flatten it and break both.
        _engine.get_download_fn()(name, cache_dir=str(staging))

        if cancel_event is not None and cancel_event.is_set():
            # Cancelled after the transfer completed; discard the staged copy.
            return

        # Promote each top-level staged entry into models_root atomically.
        for entry in staging.iterdir():
            dest = models_root / entry.name
            if entry.is_dir():
                atomic_replace_dir(entry, dest)
            else:
                os.replace(entry, dest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
