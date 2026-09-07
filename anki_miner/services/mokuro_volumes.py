"""Find manga volumes for Utilities → Manga OCR.

Mirrors mokuro's own path rules (``mokuro/run.py`` ``--parent_dir`` scan and
``mokuro/volume.py:get_path_mokuro``) so what the tab lists is what mokuro
would process, and the ``.mokuro`` it predicts is the one mokuro writes:

* a folder holding page images directly is ONE volume;
* otherwise each child folder that contains an image anywhere (mokuro globs
  recursively), skipping ``_ocr`` and OS junk, and each ``.cbz``/``.zip``
  child is one volume, natural-sorted;
* ``Vol1/`` and ``Vol1.cbz`` share ``Vol1.mokuro`` and count once.

Pure filesystem reads; runs off the GUI thread (``run_off_thread`` in the tab,
the worker otherwise).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from anki_miner.services.reading._util import is_junk_path, natural_sort_key

#: mokuro's ``Volume.get_img_paths`` set (one wider than the Reading loader's).
MOKURO_IMAGE_EXTENSIONS: frozenset[str] = frozenset({".jpg", ".jpeg", ".png", ".webp", ".avif"})
MOKURO_ARCHIVE_EXTENSIONS: frozenset[str] = frozenset({".cbz", ".zip"})
_OCR_CACHE_DIRNAME = "_ocr"


@dataclass(frozen=True)
class MokuroVolume:
    """One thing mokuro treats as a volume, and where its ``.mokuro`` lands."""

    source: Path
    output: Path
    already_processed: bool


def mokuro_output_path(source: Path) -> Path:
    """The ``.mokuro`` mokuro writes for *source* (``get_path_mokuro``)."""
    if source.is_dir():
        return source.parent / f"{source.name}.mokuro"
    return source.with_suffix(".mokuro")


def _is_image_name(name: str) -> bool:
    return Path(name).suffix.lower() in MOKURO_IMAGE_EXTENSIONS and not is_junk_path(name)


def _has_images_within(directory: Path) -> bool:
    for _root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d != _OCR_CACHE_DIRNAME and not is_junk_path(d)]
        if any(_is_image_name(f) for f in files):
            return True
    return False


def _volume(source: Path) -> MokuroVolume:
    output = mokuro_output_path(source)
    return MokuroVolume(source=source, output=output, already_processed=output.is_file())


def scan_volumes(folder: Path) -> list[MokuroVolume]:
    """Volumes mokuro would process for *folder*; ``[]`` when there are none."""
    entries = sorted(folder.iterdir(), key=lambda p: natural_sort_key(p.name))
    if any(p.is_file() and _is_image_name(p.name) for p in entries):
        return [_volume(folder)]
    volumes: list[MokuroVolume] = []
    seen_outputs: set[Path] = set()
    for entry in entries:
        if is_junk_path(entry.name):
            continue
        if entry.is_dir():
            if entry.name == _OCR_CACHE_DIRNAME or not _has_images_within(entry):
                continue
        elif not (entry.is_file() and entry.suffix.lower() in MOKURO_ARCHIVE_EXTENSIONS):
            continue
        volume = _volume(entry)
        if volume.output in seen_outputs:
            continue
        seen_outputs.add(volume.output)
        volumes.append(volume)
    return volumes
