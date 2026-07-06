"""Input classification and load dispatch for the reading-tab pipeline.

The GUI hands dropped paths to :func:`detect`, which classifies each into one or
more :class:`ReadingSourceRef` items (one manga volume or one novel file each).
The queue worker later calls :func:`load` per ref, which lazily dispatches to the
matching source loader (``mokuro_source`` / ``epub_source`` / ``aozora_source``).

For ``kind="mokuro"`` refs, ``detect`` is the *sole* metadata reader: it validates
the ``.mokuro`` JSON sidecar and fully populates ``title`` (series), ``volume``
(episode) and ``image_root`` (archive Path / directory Path / None for text-only),
so the loaders can trust the ref. For ``epub``/``txt`` refs it classifies purely by
extension without opening the file, leaving the loader authoritative for the final
document metadata.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from anki_miner.exceptions import SetupError

from ._util import is_junk_path, natural_sort_key
from .models import ReadingDocument, ReadingSourceRef

# Required top-level keys in a ``.mokuro`` sidecar. Unknown keys are ignored —
# community files carry extras like ``chars``/``spine_width``.
_MOKURO_REQUIRED_KEYS: tuple[str, ...] = (
    "version",
    "title",
    "title_uuid",
    "volume",
    "volume_uuid",
    "pages",
)

# Image-archive extensions a ``.mokuro`` volume may be backed by, in precedence
# order (``.cbz`` before ``.zip``); matched case-insensitively.
_ARCHIVE_EXTS: tuple[str, ...] = (".cbz", ".zip")


def detect(path: Path) -> list[ReadingSourceRef]:
    """Classify a dropped path into loadable reading sources.

    Cascade (first match wins):

    1. ``*.mokuro`` file → one manga volume.
    2. ``.cbz``/``.zip`` → its sibling ``.mokuro`` (required, else error).
    3. directory → its ``*.mokuro`` children (title dir), else the sibling
       ``<name>.mokuro`` (user dropped the image dir itself), else error.
    4. ``.epub``/``.txt`` → one book (metadata deferred to the loader).

    Raises :class:`SetupError` on unusable input (missing sidecar, invalid
    ``.mokuro`` JSON/schema, unrecognized path).
    """
    suffix = path.suffix.lower()

    if suffix == ".mokuro":
        return [_mokuro_ref(path)]
    if suffix in _ARCHIVE_EXTS:
        return _detect_archive(path)
    if path.is_dir():
        return _detect_directory(path)
    if suffix == ".epub":
        return [_book_ref(path, "epub")]
    if suffix == ".txt":
        return [_book_ref(path, "txt")]

    raise SetupError(
        f"'{path.name}' is not a recognized reading source. Supported: .mokuro, "
        ".cbz/.zip (with a matching .mokuro), .epub, .txt, or a folder of "
        ".mokuro volumes."
    )


def load(ref: ReadingSourceRef) -> ReadingDocument:
    """Dispatch a ref to its source loader and return the loaded document.

    Imports the per-kind loader lazily inside the branch so importing this
    module stays cheap and a broken/absent loader can't fail unrelated kinds.
    """
    if ref.kind == "mokuro":
        from . import mokuro_source

        return mokuro_source.load(ref)
    if ref.kind == "epub":
        from . import epub_source

        return epub_source.load(ref)
    if ref.kind == "txt":
        from . import aozora_source

        return aozora_source.load(ref)

    raise SetupError(f"Unknown reading source kind: {ref.kind!r}")


# --------------------------------------------------------------------------- #
# Private helpers.
# --------------------------------------------------------------------------- #


def _mokuro_ref(mokuro_path: Path) -> ReadingSourceRef:
    """Build a fully-populated mokuro-volume ref from a ``.mokuro`` sidecar."""
    meta = _read_mokuro_meta(mokuro_path)
    return ReadingSourceRef(
        kind="mokuro",
        path=mokuro_path,
        image_root=_resolve_image_root(mokuro_path),
        title=str(meta["title"]),
        volume=str(meta["volume"]),
    )


def _book_ref(path: Path, kind: Literal["epub", "txt"]) -> ReadingSourceRef:
    """Build a provisional book ref by extension alone (no file open)."""
    return ReadingSourceRef(
        kind=kind,
        path=path,
        image_root=None,
        title=path.stem,
        volume=None,
    )


def _detect_archive(archive_path: Path) -> list[ReadingSourceRef]:
    """A dropped ``.cbz``/``.zip`` resolves through its sibling ``.mokuro``."""
    sidecar = archive_path.with_suffix(".mokuro")
    if sidecar.is_file():
        return [_mokuro_ref(sidecar)]
    raise SetupError(f"No .mokuro sidecar found for '{archive_path.name}'. " f"Expected '{sidecar.name}' alongside it.")


def _detect_directory(directory: Path) -> list[ReadingSourceRef]:
    """A dropped directory is a title dir, a dropped image dir, or not mokuro."""
    children = sorted(
        (
            child
            for child in directory.iterdir()
            if child.is_file() and child.suffix.lower() == ".mokuro" and not is_junk_path(child.name)
        ),
        key=lambda child: natural_sort_key(child.name),
    )
    if children:
        return [_mokuro_ref(child) for child in children]

    # User dropped the image dir itself: look for a sibling "<name>.mokuro".
    sidecar = directory.parent / (directory.name + ".mokuro")
    if sidecar.is_file():
        return [_mokuro_ref(sidecar)]

    raise SetupError(
        f"'{directory.name}' is not a recognized reading source: no .mokuro "
        "volumes inside it and no matching .mokuro sidecar beside it."
    )


def _resolve_image_root(mokuro_path: Path) -> Path | None:
    """Locate a ``.mokuro`` volume's page images.

    Precedence: a sibling ``<stem>/`` directory beats a ``<stem>.cbz``/``.zip``
    archive (extension case-insensitive, ``.cbz`` before ``.zip``); ``None`` if
    neither exists (a text-only volume).
    """
    parent = mokuro_path.parent
    stem = mokuro_path.stem

    dir_candidate = parent / stem
    if dir_candidate.is_dir():
        return dir_candidate

    if parent.is_dir():
        archives = [
            entry
            for entry in parent.iterdir()
            if entry.is_file() and entry.stem == stem and entry.suffix.lower() in _ARCHIVE_EXTS
        ]
        if archives:
            archives.sort(key=lambda entry: _ARCHIVE_EXTS.index(entry.suffix.lower()))
            return archives[0]

    return None


def _read_mokuro_meta(mokuro_path: Path) -> dict[str, Any]:
    """Read + validate a ``.mokuro`` sidecar, returning its parsed JSON dict.

    Raises :class:`SetupError` on unreadable file, invalid JSON, a non-object
    top level, or any missing required key.
    """
    try:
        raw = mokuro_path.read_text(encoding="utf-8")
    except OSError as e:
        raise SetupError(f"Cannot read .mokuro file '{mokuro_path.name}': {e}") from e

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SetupError(f"Invalid .mokuro file '{mokuro_path.name}': {e}") from e

    if not isinstance(data, dict):
        raise SetupError(f"Invalid .mokuro file '{mokuro_path.name}': " "expected a JSON object at the top level.")

    missing = [key for key in _MOKURO_REQUIRED_KEYS if key not in data]
    if missing:
        raise SetupError(
            f"Invalid .mokuro file '{mokuro_path.name}': " f"missing required key(s): {', '.join(missing)}."
        )

    return data
