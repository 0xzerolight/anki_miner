"""Input classification and load dispatch for the reading-tab pipeline.

The GUI hands dropped paths to :func:`detect`, which classifies each into one or
more :class:`ReadingSourceRef` items (one manga volume, novel file, or subtitle
file each). The queue worker later calls :func:`load` per ref, which lazily
dispatches to the matching source loader (``mokuro_source`` / ``epub_source`` /
``aozora_source`` / ``subtitle_source``).

For ``kind="mokuro"`` refs, ``detect`` is the *sole* metadata reader: it validates
the ``.mokuro`` JSON sidecar and fully populates ``title`` (series), ``volume``
(episode) and ``image_root`` (archive Path / directory Path / None for text-only),
so the loaders can trust the ref. For ``epub``/``txt`` refs it classifies purely by
extension without opening the file, leaving the loader authoritative for the final
document metadata.
"""

from __future__ import annotations

import json
import logging
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from anki_miner.exceptions import OperationCancelled, SetupError
from anki_miner.models.reading import ReadingDocument, ReadingSourceRef
from anki_miner.utils.logging_ext import log_summary

from ._util import (
    MAX_MOKURO_JSON_BYTES,
    is_junk_path,
    natural_sort_key,
    read_text_capped,
    read_zip_member_text_capped,
)

if TYPE_CHECKING:
    from anki_miner.languages.profile import SentenceRules

logger = logging.getLogger(__name__)

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

# Subtitle-file extensions mined as text (Reading → Subtitle Files sub-tab). No
# MicroDVD ``.sub``: frame-based, pysubs2 needs a media-derived fps we don't
# have without the video.
_SUBTITLE_EXTS: tuple[str, ...] = (".srt", ".ass", ".ssa", ".vtt")

# Book-file extensions (Reading → Novels sub-tab); matched case-insensitively.
_BOOK_EXTS: tuple[str, ...] = (".epub", ".txt")


def detect(
    path: Path,
    *,
    diagnostics: list[tuple[Path, str]] | None = None,
) -> list[ReadingSourceRef]:
    """Classify a dropped path into loadable reading sources.

    Cascade (first match wins):

    1. ``*.mokuro`` file → one manga volume.
    2. ``.cbz``/``.zip`` → its sibling ``.mokuro``, else an embedded
       ``.mokuro`` member inside the archive (Issue #103), else error.
    3. directory → its ``*.mokuro`` children and embedded-``.mokuro``
       archives (title dir), else the sibling ``<name>.mokuro`` (user
       dropped the image dir itself), else error.
    4. ``.epub``/``.txt`` → one book (metadata deferred to the loader).
    5. ``.srt``/``.ass``/``.ssa``/``.vtt`` → one subtitle document (metadata
       deferred to the loader).

    ``diagnostics`` receives ``(archive_path, reason)`` entries for malformed
    embedded volumes skipped during a folder scan. Raises :class:`SetupError`
    on unusable input (missing sidecar, invalid ``.mokuro`` JSON/schema,
    unrecognized path).
    """
    suffix = path.suffix.lower()
    is_directory = path.is_dir()

    try:
        if suffix == ".mokuro":
            refs = [_mokuro_ref(path)]
            _log_detected(path, refs, format_="mokuro", reason="marker_file", marker=path.name)
            return refs
        if suffix in _ARCHIVE_EXTS:
            refs = _detect_archive(path)
            ref = refs[0]
            if ref.ocr_entry is not None:
                reason = "embedded_marker"
                marker = Path(ref.ocr_entry).name
            else:
                reason = "sibling_marker"
                marker = ref.path.name if ref.path is not None else "-"
            _log_detected(path, refs, format_="mokuro", reason=reason, marker=marker)
            return refs
        if is_directory:
            degraded_archives = diagnostics if diagnostics is not None else []
            diagnostic_start = len(degraded_archives)
            refs = _detect_directory(path, degraded_archives=degraded_archives)
            markers = [Path(ref.ocr_entry).name if ref.ocr_entry else ref.path.name for ref in refs if ref.path]
            reason = (
                "directory_children" if all(ref.path and ref.path.parent == path for ref in refs) else "sibling_marker"
            )
            _log_detected(
                path,
                refs,
                format_="mokuro",
                reason=reason,
                marker=markers,
                skipped_archives=len(degraded_archives) - diagnostic_start,
            )
            return refs
        if suffix == ".epub":
            refs = [_book_ref(path, "epub")]
            _log_detected(path, refs, format_="epub", reason="extension", marker=suffix)
            return refs
        if suffix == ".txt":
            refs = [_book_ref(path, "txt")]
            _log_detected(path, refs, format_="txt", reason="extension", marker=suffix)
            return refs
        if suffix in _SUBTITLE_EXTS:
            refs = [_subtitle_ref(path)]
            _log_detected(path, refs, format_="subtitle", reason="extension", marker=suffix)
            return refs

        raise SetupError(
            f"'{path.name}' is not a recognized reading source. Supported: .mokuro, "
            ".cbz/.zip (with a matching .mokuro beside or inside it), .epub, .txt, "
            "subtitle files (.srt/.ass/.ssa/.vtt), or a folder of .mokuro volumes."
        )
    except SetupError:
        log_summary(
            logger,
            "Reading detect failed",
            level=logging.WARNING,
            input=path,
            found=_detection_failure_found(suffix, is_directory),
        )
        raise
    except OSError as e:
        log_summary(
            logger,
            "Reading detect failed",
            level=logging.WARNING,
            input=path,
            found="unreadable_path",
            error=type(e).__name__,
        )
        raise


def load(
    ref: ReadingSourceRef,
    *,
    cancel_check: Callable[[], bool] | None = None,
    encodings: tuple[str, ...] | None = None,
    rules: SentenceRules | None = None,
) -> ReadingDocument:
    """Dispatch a ref to its source loader and return the loaded document.

    Imports the per-kind loader lazily inside the branch so importing this
    module stays cheap and a broken/absent loader can't fail unrelated kinds.
    ``kind="text"`` refs are pathless (built by the Text sub-tab, never by
    :func:`detect`) and carry their content in ``ref.text``.

    ``encodings`` is the mining language's decode ladder, and reaches only the
    two loaders that guess an encoding — the novel and subtitle ones. mokuro
    reads a UTF-8 JSON sidecar, EPUB takes its encoding from the archive's own
    XML declaration, and a ``text`` ref is already a decoded string, so none of
    the other three accepts the keyword at all.

    ``rules`` is that language's sentence-splitting policy, and reaches the four
    loaders that call ``split_sentences``. ``subtitle`` is the odd one out the
    other way round: it splits nothing, so it takes the ladder but not the
    rules.

    Each optional argument is built as its own fragment and omitted when
    ``None``, so a call that supplies none is the pre-transition
    ``loader.load(ref)`` verbatim and no branch is handed a keyword its loader
    does not accept.
    """
    if cancel_check is not None and cancel_check():
        raise OperationCancelled("Reading load cancelled")
    common: dict[str, Any] = {} if cancel_check is None else {"cancel_check": cancel_check}
    sniffing: dict[str, Any] = {} if encodings is None else {"encodings": encodings}
    splitting: dict[str, Any] = {} if rules is None else {"rules": rules}
    if ref.kind == "mokuro":
        from . import mokuro_source

        return mokuro_source.load(ref, **common, **splitting)
    if ref.kind == "epub":
        from . import epub_source

        return epub_source.load(ref, **common, **splitting)
    if ref.kind == "txt":
        from . import aozora_source

        return aozora_source.load(ref, **common, **sniffing, **splitting)
    if ref.kind == "subtitle":
        from . import subtitle_source

        return subtitle_source.load(ref, **common, **sniffing)
    if ref.kind == "text":
        from . import text_source

        return text_source.load(ref, **common, **splitting)

    raise SetupError(f"Unknown reading source kind: {ref.kind!r}")


def detect_book_folder(directory: Path) -> list[ReadingSourceRef]:
    """Enumerate the top-level ``.epub``/``.txt`` books in a directory.

    The Novels sub-tab's folder-mining path: one ref per book, natural-sorted
    by filename. Deliberately non-recursive — a folder pick must not sweep a
    whole library through its subfolders. Refs are provisional (extension
    only, no file open), so the per-book loader stays authoritative for
    metadata and the EPUB DRM check: one protected book fails its own queue
    item at mine time instead of poisoning the scan.

    Separate from :func:`detect`'s directory branch on purpose — that cascade
    stays mokuro-only so the manga tab's folder semantics are unchanged.

    Raises :class:`SetupError` on an unreadable path or a folder with no
    top-level books.
    """
    try:
        entries = list(directory.iterdir())
    except OSError as e:
        log_summary(
            logger,
            "Reading detect failed",
            level=logging.WARNING,
            input=directory,
            found="unreadable_directory",
            error=type(e).__name__,
        )
        raise SetupError(f"Cannot read folder '{directory.name}': {e}") from e

    books = sorted(
        (
            child
            for child in entries
            if child.is_file() and child.suffix.lower() in _BOOK_EXTS and not is_junk_path(child.name)
        ),
        key=lambda child: natural_sort_key(child.name),
    )
    if not books:
        found_extensions = sorted({child.suffix.lower() for child in entries if child.is_file() and child.suffix})
        log_summary(
            logger,
            "Reading detect failed",
            level=logging.WARNING,
            input=directory,
            found=found_extensions,
        )
        raise SetupError(
            f"No .epub or .txt books found in '{directory.name}'. Manga folders are mined in the Manga tab."
        )
    refs = [_book_ref(child, "epub" if child.suffix.lower() == ".epub" else "txt") for child in books]
    _log_detected(
        directory,
        refs,
        format_="books",
        reason="top_level_extensions",
        marker=sorted({child.suffix.lower() for child in books}),
    )
    return refs


# --------------------------------------------------------------------------- #
# Private helpers.
# --------------------------------------------------------------------------- #


def _log_detected(
    path: Path,
    refs: list[ReadingSourceRef],
    *,
    format_: str,
    reason: str,
    marker: object,
    skipped_archives: int | None = None,
) -> None:
    """Emit the single successful public-detection receipt."""
    log_summary(
        logger,
        "Reading detect",
        input=path,
        format=format_,
        reason=reason,
        marker=marker,
        sources=len(refs),
        skipped_archives=skipped_archives if skipped_archives is not None else 0,
    )


def _detection_failure_found(suffix: str, is_directory: bool) -> str:
    """Bounded description of the shape that failed classification."""
    if is_directory:
        return "directory_without_mokuro"
    if suffix == ".mokuro":
        return "invalid_mokuro_marker"
    if suffix in _ARCHIVE_EXTS:
        return "archive_without_usable_mokuro"
    if suffix:
        return f"extension:{suffix}"
    return "path_without_extension"


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


def _subtitle_ref(path: Path) -> ReadingSourceRef:
    """Build a provisional subtitle ref by extension alone (no file open)."""
    return ReadingSourceRef(
        kind="subtitle",
        path=path,
        image_root=None,
        title=path.stem,
        volume=None,
    )


def _detect_archive(archive_path: Path) -> list[ReadingSourceRef]:
    """A dropped ``.cbz``/``.zip`` resolves through its ``.mokuro`` OCR data.

    The sibling sidecar always wins (the standard mokuro-CLI layout). Exact
    lowercase wins among extension case variants; otherwise lexicographic
    filename order breaks ties. A self-contained archive carrying its
    ``.mokuro`` as a member is the fallback (Issue #103).
    """
    sidecar = archive_path.with_suffix(".mokuro")
    if not sidecar.is_file() and archive_path.parent.is_dir():
        sidecars = sorted(
            (
                entry
                for entry in archive_path.parent.iterdir()
                if entry.is_file() and entry.stem == archive_path.stem and entry.suffix.lower() == ".mokuro"
            ),
            key=lambda entry: entry.name,
        )
        if sidecars:
            sidecar = sidecars[0]
    if sidecar.is_file():
        return [_mokuro_ref(sidecar)]
    embedded = _embedded_mokuro_ref(archive_path, strict=True)
    if embedded is not None:
        return [embedded]
    raise SetupError(
        f"No .mokuro data found for '{archive_path.name}'. Expected '{sidecar.name}' "
        "alongside it, or a .mokuro member inside the archive."
    )


def _detect_directory(
    directory: Path,
    *,
    degraded_archives: list[tuple[Path, str]] | None = None,
) -> list[ReadingSourceRef]:
    """A dropped directory is a title dir, a dropped image dir, or not mokuro.

    A title dir's volumes are its ``.mokuro`` children plus any self-contained
    ``.cbz``/``.zip`` archives carrying an embedded ``.mokuro`` member. A
    sidecar covers its same-stem archive (one volume, not two), and among
    embedded-only same-stem archives ``.cbz`` beats ``.zip`` (mirroring
    :func:`_resolve_image_root`). A broken/OCR-less archive contributes
    nothing — it must never abort the folder scan.
    """
    children = sorted(
        (
            child
            for child in directory.iterdir()
            if child.is_file() and child.suffix.lower() == ".mokuro" and not is_junk_path(child.name)
        ),
        key=lambda child: natural_sort_key(child.name),
    )
    sidecar_stems = {child.stem for child in children}
    archives = sorted(
        (
            child
            for child in directory.iterdir()
            if child.is_file()
            and child.suffix.lower() in _ARCHIVE_EXTS
            and child.stem not in sidecar_stems
            and not is_junk_path(child.name)
        ),
        # Same-stem .cbz/.zip pairs: keep the first per stem, .cbz preferred.
        key=lambda child: (
            natural_sort_key(child.stem),
            _ARCHIVE_EXTS.index(child.suffix.lower()),
            natural_sort_key(child.name),
        ),
    )
    seen_stems: set[str] = set()
    embedded_pairs: list[tuple[Path, ReadingSourceRef]] = []
    for archive in archives:
        if archive.stem in seen_stems:
            continue
        seen_stems.add(archive.stem)
        ref = _embedded_mokuro_ref(
            archive,
            strict=False,
            degraded_archives=degraded_archives,
        )
        if ref is not None:
            embedded_pairs.append((archive, ref))

    if children or embedded_pairs:
        volumes = [(child, _mokuro_ref(child)) for child in children] + embedded_pairs
        volumes.sort(key=lambda pair: natural_sort_key(pair[0].name))
        return [ref for _, ref in volumes]

    # User dropped the image dir itself: prefer an exact "<name>.mokuro",
    # then the first filename-sorted exact-stem extension case variant.
    sidecar = directory.parent / (directory.name + ".mokuro")
    if not sidecar.is_file():
        sidecars = sorted(
            (
                entry
                for entry in directory.parent.iterdir()
                if entry.is_file() and entry.stem == directory.name and entry.suffix.lower() == ".mokuro"
            ),
            key=lambda entry: entry.name,
        )
        if sidecars:
            sidecar = sidecars[0]
    if sidecar.is_file():
        return [_mokuro_ref(sidecar)]

    raise SetupError(
        f"'{directory.name}' is not a recognized reading source: no .mokuro "
        "volumes or embedded-.mokuro archives inside it and no matching "
        ".mokuro sidecar beside it."
    )


def _embedded_mokuro_ref(
    archive_path: Path,
    *,
    strict: bool,
    degraded_archives: list[tuple[Path, str]] | None = None,
) -> ReadingSourceRef | None:
    """Probe an archive for exactly one embedded ``.mokuro`` member.

    Returns a fully-populated ref (``path`` = ``image_root`` = the archive,
    ``ocr_entry`` = the member) or ``None`` when the archive has no ``.mokuro``
    member. Failure containment is the caller's contract:

    * ``strict=True`` (a directly-selected archive): ambiguous/unreadable
      archives and malformed members raise :class:`SetupError` so the user
      sees why their pick failed.
    * ``strict=False`` (a title-dir scan): ANY failure returns ``None`` — one
      broken archive in a series folder must never abort the whole scan.
    """
    try:
        try:
            with zipfile.ZipFile(archive_path) as zf:
                members = [
                    name for name in zf.namelist() if name.lower().endswith(".mokuro") and not is_junk_path(name)
                ]
        except (zipfile.BadZipFile, OSError) as e:
            if strict:
                logger.debug(
                    "Reading archive probe failed: archive=%s error=%s detail=%s",
                    archive_path,
                    type(e).__name__,
                    e,
                )
            raise SetupError(f"Cannot read archive '{archive_path.name}': {e}") from e
        if not members:
            return None
        if len(members) > 1:
            raise SetupError(
                f"'{archive_path.name}' contains multiple .mokuro members "
                f"({', '.join(sorted(members))}); expected exactly one volume."
            )
        entry = members[0]
        meta = _parse_mokuro_meta(
            read_zip_member_text_capped(
                archive_path,
                entry,
                MAX_MOKURO_JSON_BYTES,
                ".mokuro member",
                log_failures=strict,
            ),
            f"{archive_path.name}:{entry}",
            log_failure=strict,
        )
    except SetupError as exc:
        if strict:
            raise
        # A title directory may contain hundreds of bad archives. Preserve
        # first-N identities at DEBUG; the public summary carries total count.
        if degraded_archives is None or len(degraded_archives) < 5:
            logger.debug(
                "Reading archive probe skipped: archive=%s error=%s detail=%s",
                archive_path,
                type(exc).__name__,
                exc,
            )
        if degraded_archives is not None:
            degraded_archives.append((archive_path, str(exc)))
        return None
    return ReadingSourceRef(
        kind="mokuro",
        path=archive_path,
        image_root=archive_path,
        title=str(meta["title"]),
        volume=str(meta["volume"]),
        ocr_entry=entry,
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
        # Size-capped (module global so tests can shrink it): a hostile
        # multi-GB sidecar must fail fast instead of OOMing the load.
        raw = read_text_capped(mokuro_path, MAX_MOKURO_JSON_BYTES, ".mokuro file")
    except OSError as e:
        logger.debug(
            "Mokuro metadata read failed: file=%s error=%s detail=%s",
            mokuro_path,
            type(e).__name__,
            e,
        )
        raise SetupError(f"Cannot read .mokuro file '{mokuro_path.name}': {e}") from e
    return _parse_mokuro_meta(raw, mokuro_path.name)


def _parse_mokuro_meta(
    raw: str,
    display_name: str,
    *,
    log_failure: bool = True,
) -> dict[str, Any]:
    """Validate ``.mokuro`` JSON text (sidecar file or archive member).

    Carries ALL the schema validation — JSON parse, object top level,
    required keys, and pages-is-an-array — so the sidecar and embedded
    paths cannot drift.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        if log_failure:
            logger.debug(
                "Mokuro metadata parse failed: file=%s error=%s line=%d column=%d",
                display_name,
                type(e).__name__,
                e.lineno,
                e.colno,
            )
        raise SetupError(f"Invalid .mokuro file '{display_name}': {e}") from e

    if not isinstance(data, dict):
        raise SetupError(f"Invalid .mokuro file '{display_name}': expected a JSON object at the top level.")

    missing = [key for key in _MOKURO_REQUIRED_KEYS if key not in data]
    if missing:
        raise SetupError(f"Invalid .mokuro file '{display_name}': missing required key(s): {', '.join(missing)}.")
    if not isinstance(data["pages"], list):
        raise SetupError(f"Invalid .mokuro file '{display_name}': pages must be an array.")

    return data
