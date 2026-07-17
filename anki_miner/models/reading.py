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

    ``block_box`` is the mokuro block bounding box in original-page pixel
    coords (xmin, ymin, xmax, ymax); None for novels/txt and malformed blocks.
    Sentence-split pieces of one oversized block share the parent block's box.
    """

    text: str
    index: int
    location_label: str
    image_ref: ImageRef | None = None
    block_box: tuple[int, int, int, int] | None = None


@dataclass(frozen=True)
class ReadingSourceRef:
    """A detected, loadable source: one manga volume, novel file, or pasted text.

    Per-kind population contract:

    * kind="mokuro": the detector fully populates every field from the
      ``.mokuro`` JSON — ``title`` (= series), ``volume`` (= episode), and
      ``image_root`` (archive file Path for .cbz/.zip-backed volumes,
      directory Path for dir-backed, None for text-only). Loaders trust these.
    * kind in {"epub","txt","subtitle"}: the detector sets ``title`` =
      ``path.stem`` (a provisional label for queue rows only), ``volume`` =
      None and ``image_root`` = None; the loader is authoritative for the
      final ``ReadingDocument`` metadata.
    * kind="text": built directly by the Text sub-tab, never by the detector —
      ``text`` holds the pasted content, ``title`` = "Text", and ``path`` /
      ``image_root`` / ``volume`` are None. Distinct from kind="txt", which is
      a ``.txt`` *file* on disk (aozora loader).

    ``path`` is always set for the file-backed kinds (their loaders assert
    this) and only None for kind="text".
    """

    kind: Literal["mokuro", "epub", "txt", "subtitle", "text"]
    path: Path | None = None
    image_root: Path | None = None
    title: str = ""
    volume: str | None = None
    text: str | None = None

    def __post_init__(self) -> None:
        # Every field defaults so kind="text" can be built positionally, but the
        # file-backed kinds must carry a path — their loaders assert it, and that
        # assert is stripped under `python -O`. Enforce the invariant at
        # construction so a malformed ref fails loudly at its source instead.
        if self.kind != "text" and self.path is None:
            raise ValueError(f"ReadingSourceRef(kind={self.kind!r}) requires a path")


@dataclass
class ReadingDocument:
    """A fully loaded document: ordered units plus load-time warnings.

    Not frozen — it carries mutable ``units``/``warnings`` populated during
    loading. ``warnings`` (text-only volume, unmatched pages, gaiji-image
    count, unusable cover, …) are surfaced up front by ``process_reading``.
    """

    title: str
    kind: Literal["manga", "book", "subtitle"]
    series: str
    episode: str
    units: list[ReadingUnit] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
