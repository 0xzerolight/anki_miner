"""Data models for the reading-tab pipeline (manga volumes + novels)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class ImageRef:
    """Deferred reference to a page/cover image, materialized in phase3'.

    Frozen + hashable so materialization dedups per unique ref (a page shared
    by many words converts once). Two shapes, told apart by ``entry``:

    * directory/file page — ``ImageRef(image_path)``: ``source`` is the image
      file on disk, ``entry`` is None.
    * archive page/cover — ``ImageRef(archive_path, entry_name)``: ``source``
      is the containing ``.cbz``/``.zip``/``.epub`` archive and ``entry`` is
      the member name. No bytes are extracted at load time.

    The two shapes compare and hash distinctly because ``entry`` is None for
    on-disk pages and a str for archive members.
    """

    source: Path
    entry: str | None = None


@dataclass(frozen=True)
class ReadingUnit:
    """One mining unit: a text span with its document position and image.

    ``index`` is document order and doubles as the dummy card start_time.
    ``location_label`` is a human page/chapter tag ("p.42" / "ch.3"). Frozen
    so a unit's ``image_ref`` can participate in per-ref materialization dedup.
    """

    text: str
    index: int
    location_label: str
    image_ref: ImageRef | None = None


@dataclass(frozen=True)
class ReadingSourceRef:
    """A detected, loadable source: one manga volume or one novel file.

    Per-kind population contract:

    * kind="mokuro": the detector fully populates every field from the
      ``.mokuro`` JSON — ``title`` (= series), ``volume`` (= episode), and
      ``image_root`` (archive file Path for .cbz/.zip-backed volumes,
      directory Path for dir-backed, None for text-only). Loaders trust these.
    * kind in {"epub","txt"}: the detector sets ``title`` = ``path.stem`` (a
      provisional label for queue rows only), ``volume`` = None and
      ``image_root`` = None; the loader is authoritative for the final
      ``ReadingDocument`` metadata.
    """

    kind: Literal["mokuro", "epub", "txt"]
    path: Path
    image_root: Path | None
    title: str
    volume: str | None


@dataclass
class ReadingDocument:
    """A fully loaded document: ordered units plus load-time warnings.

    Not frozen — it carries mutable ``units``/``warnings`` populated during
    loading. ``warnings`` (text-only volume, unmatched pages, gaiji-image
    count, unusable cover, …) are surfaced up front by ``process_reading``.
    """

    title: str
    kind: Literal["manga", "book"]
    series: str
    episode: str
    units: list[ReadingUnit] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
