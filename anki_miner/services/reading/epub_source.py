"""Load one ``.epub`` novel into a :class:`ReadingDocument`.

Pure ``zipfile`` + ``lxml`` — no ``ebooklib`` (AGPL, and it adds nothing over
walking the container/OPF ourselves). The flow mirrors the reader spec:

1. ``META-INF/container.xml`` → the OPF package path.
2. OPF → manifest (id → href/media-type/properties), ordered spine (``linear``
   ``no`` skipped), ``dc:title``/``dc:creator``.
3. ``META-INF/encryption.xml`` — content encryption (anything other than the
   two IDPF/Adobe font-obfuscation algorithms, or a cipher aimed at a manifest-
   declared font resource) is DRM: abort with a clear error naming the file.
   Font obfuscation is benign and the book mines normally.
4. Cover → an EPUB3 ``cover-image`` manifest property or the EPUB2
   ``<meta name="cover">`` id. A fixed-size magic-byte peek validates the entry
   without decoding it; on any failure the book still mines, cover-less, with a
   recorded warning.
5. Chapters → the EPUB3 nav document or the EPUB2 NCX; boilerplate labels
   (表紙/目次/…) dropped; fewer than two usable entries falls back to spine index.
   Front/back matter (TS-08) → EPUB3 landmarks, the EPUB2 ``<guide>`` and a
   spine document's own ``epub:type``; skipped only near either end of the book.
6. Each spine XHTML → base text (ruby readings and ``<img>`` gaiji dropped),
   paragraph-split on block close / ``<br>`` → ``sentence_splitter`` → units.
   Each ``<ruby>``'s (base, reading) pair rides on the unit whose sentence holds
   the base (``ReadingUnit.ruby``, SK-31); the text itself never carries it.

Loading decodes no image bytes and never materializes a page — the one disk
touch beyond text is the bomb-safe cover peek. The single shared cover
:class:`ImageRef` rides on every unit (books put the cover on every card).
"""

from __future__ import annotations

import logging
import lzma
import math
import posixpath
import re
import zipfile
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

from lxml import etree, html  # type: ignore[import-untyped]

from anki_miner.exceptions import OperationCancelled, SetupError, raise_if_cancelled
from anki_miner.models.reading import (
    ImageRef,
    ReadingDocument,
    ReadingSourceRef,
    ReadingUnit,
)
from anki_miner.services.dictionary.zip_safety import MAX_UNCOMPRESSED_BYTES, validate_zip_safe

# _line_join's home is _util (shared with the plain-text loaders); imported
# here both for load() and as a re-export for tests that call it.
from anki_miner.services.reading._util import READING_CANCELLED, _line_join
from anki_miner.services.reading.sentence_splitter import split_sentences
from anki_miner.utils.logging_ext import log_summary

if TYPE_CHECKING:
    from anki_miner.languages.profile import SentenceRules

logger = logging.getLogger(__name__)

_CONTAINER_PATH = "META-INF/container.xml"
_ENCRYPTION_PATH = "META-INF/encryption.xml"
_OPF_MEDIA_TYPE = "application/oebps-package+xml"

# Namespaced attribute names that survive both the XML and the lxml.html parser.
_EPUB_TYPE_ATTRS = ("{http://www.idpf.org/2007/ops}type", "epub:type")

# Encryption algorithms that merely obfuscate embedded fonts — safe to mine.
_FONT_OBFUSCATION_ALGS = frozenset({"http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"})
_FONT_EXTS = (".otf", ".ttf", ".ttc", ".woff", ".woff2", ".eot", ".dfont")
_FONT_MEDIA_TYPES = frozenset(f"font/{ext[1:]}" for ext in _FONT_EXTS) | frozenset(
    {
        "font/collection",
        "application/vnd.ms-opentype",
        "application/font-woff",
        # Deprecated EPUB 3.3 core alias for TrueType/OpenType resources —
        # still emitted by older packaging tools.
        "application/font-sfnt",
    }
)

# Subtrees whose text is never body prose (ruby readings live in rt/rp).
_SKIP_TAGS = frozenset({"script", "style", "head", "rt", "rp"})
# Closing one of these — or a <br> — ends a paragraph.
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "blockquote",
        "section",
        "article",
        "td",
        "th",
        "figure",
        "figcaption",
    }
)

# Chapter labels that are structure, not content.
_BOILERPLATE_LABELS = frozenset({"表紙", "目次", "奥付", "扉", "中扉"})
# Spine filename stem tokens that mark front/back matter, not the work.
_BOILERPLATE_TOKENS = frozenset({"cover", "toc", "colophon", "caution"})
# Split a stem into whole tokens on ``-``, ``_`` and ``.`` boundaries.
_STEM_DELIMITERS = re.compile(r"[-_.]+")
# TS-08: landmark / guide / epub:type values that mark front or back matter,
# never the work: copyright, contents, index, colophon, title page, lists of
# illustrations and tables, bibliography. EPUB3 spells the title page
# "titlepage", the EPUB2 <guide> "title-page".
_MATTER_TYPES = frozenset(
    {"copyright-page", "toc", "index", "colophon", "titlepage", "title-page", "loi", "lot", "bibliography"}
)
# Matter is skipped only within this share of the spine at either end, so a
# mis-typed chapter in the middle of a book is never dropped (storyteller's zone).
_MATTER_ZONE = 0.15

_CONTENT_MEDIA_TYPES = frozenset({"application/xhtml+xml", "text/html"})
_CONTENT_EXTS = (".xhtml", ".html", ".htm")

# Pretty-printed XHTML wraps paragraph text across lines with indent. A CJK
# line-wrap joins with "" (no space) while leaving internal U+3000 untouched; a
# space-delimited language joins with " " (see _line_join), or the wrap fuses
# two words into one fabricated card front.
_INTERNAL_LINEBREAK = re.compile(r"[ \t]*\n[ \t]*")

# Cap on any single decompressed member read out of the EPUB. Every fully-read
# member is text (container/OPF/encryption/spine XHTML/nav/NCX); real chapters
# are well under 1 MiB, so 32 MiB is far above any legitimate book while still
# bounding a highly-compressible zip-bomb member. ``validate_zip_safe`` screens
# the declared archive total; the loader budget separately counts actual reads,
# including repeated spine idrefs. The cover peek stays fixed at 16 bytes.
_MAX_MEMBER_BYTES = 32 * 1024 * 1024
_MAX_TOTAL_MEMBER_BYTES = MAX_UNCOMPRESSED_BYTES
_AccountMember = Callable[[bytes], None]
_CancelCheck = Callable[[], bool]
_OPTIONAL_MEMBER_ERRORS = (
    KeyError,
    zipfile.BadZipFile,
    RuntimeError,
    NotImplementedError,
    OSError,
    EOFError,
    zlib.error,
    lzma.LZMAError,
)


def _warn_once(warnings: list[str], warning: str) -> None:
    if warning not in warnings:
        warnings.append(warning)


def _read_member(zf: zipfile.ZipFile, entry: str, epub_path: Path) -> bytes:
    """Read one zip member with a decompressed-size cap.

    Declared-size check first, then a bounded read of ``cap + 1`` bytes so an
    archive whose central directory under-declares the size cannot balloon
    memory anyway (same belt-and-suspenders as the Yomitan importer's
    ``read_yomitan_index``). Raises :class:`SetupError` over the cap;
    callers decide whether that aborts (structural members) or soft-degrades
    (spine content, nav/NCX).
    """
    info = zf.getinfo(entry)
    if info.file_size > _MAX_MEMBER_BYTES:
        raise SetupError(f"'{epub_path.name}' has a chapter too large to mine.")
    with zf.open(entry) as fp:
        data = fp.read(_MAX_MEMBER_BYTES + 1)
    if len(data) > _MAX_MEMBER_BYTES:
        raise SetupError(f"'{epub_path.name}' has a chapter too large to mine.")
    return data


def _read_member_cancellable(
    zf: zipfile.ZipFile,
    entry: str,
    epub_path: Path,
    cancel_check: _CancelCheck | None,
) -> bytes:
    """Read one member with cancellation checks on both sides of the I/O."""
    raise_if_cancelled(cancel_check, READING_CANCELLED)
    data = _read_member(zf, entry, epub_path)
    raise_if_cancelled(cancel_check, READING_CANCELLED)
    return data


def load(
    ref: ReadingSourceRef,
    *,
    cancel_check: _CancelCheck | None = None,
    rules: SentenceRules | None = None,
) -> ReadingDocument:
    """Load ``ref.path`` (an ``.epub``) into a book :class:`ReadingDocument`.

    Raises :class:`SetupError` for DRM-protected or structurally invalid files;
    soft problems (unreadable cover, gaiji images) become ``warnings`` and the
    book still mines.

    ``rules`` is the mining language's sentence-splitting policy; ``None`` is
    the splitter's built-in Japanese one.
    """
    # Per-kind ref contract: file-backed kinds always carry a path.
    raise_if_cancelled(cancel_check, READING_CANCELLED)
    assert ref.path is not None
    epub_path = ref.path
    try:
        zf = zipfile.ZipFile(epub_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise SetupError(_invalid_epub_msg(epub_path, "the ZIP archive cannot be opened")) from exc
    with zf:
        raise_if_cancelled(cancel_check, READING_CANCELLED)
        validate_zip_safe(zf, epub_path.parent)
        raise_if_cancelled(cancel_check, READING_CANCELLED)
        total_member_bytes = 0

        def account_member(raw: bytes) -> None:
            nonlocal total_member_bytes
            raise_if_cancelled(cancel_check, READING_CANCELLED)
            total_member_bytes += len(raw)
            if total_member_bytes > _MAX_TOTAL_MEMBER_BYTES:
                raise SetupError(f"'{epub_path.name}' is too large to mine.")

        names = set(zf.namelist())
        raise_if_cancelled(cancel_check, READING_CANCELLED)
        opf_path = _find_opf_path(zf, names, epub_path, account_member, cancel_check)
        opf_dir = posixpath.dirname(opf_path)
        try:
            opf_raw = _read_member_cancellable(zf, opf_path, epub_path, cancel_check)
        except _OPTIONAL_MEMBER_ERRORS as exc:
            raise SetupError(_invalid_epub_msg(epub_path, "the OPF package is unreadable")) from exc
        account_member(opf_raw)
        opf_root = _parse_xml(opf_raw)
        if opf_root is None:
            raise SetupError(_invalid_epub_msg(epub_path, "the OPF package is unreadable"))
        manifest, spine_idrefs, spine_toc, cover_meta_id, title = _parse_opf(
            opf_root,
            cancel_check=cancel_check,
        )
        _check_encryption(zf, names, epub_path, manifest, opf_dir, account_member, cancel_check)
        matter = _guide_matter(opf_root, opf_dir, cancel_check)

        doc_title = title or ref.title
        doc = ReadingDocument(title=doc_title, kind="book", series="Books", episode=doc_title)

        cover_ref, cover_warning = _find_cover(
            zf,
            names,
            manifest,
            cover_meta_id,
            opf_dir,
            epub_path,
            cancel_check,
        )
        if cover_warning:
            doc.warnings.append(cover_warning)

        chapter_map, landmark_matter = _load_chapters(
            zf,
            names,
            manifest,
            spine_toc,
            opf_dir,
            doc.warnings,
            account_member,
            cancel_check,
        )
        matter |= landmark_matter
        spine_positions = _content_positions(spine_idrefs, manifest, opf_dir)

        index = 0
        content_i = 0
        gaiji_total = 0
        matter_skipped = 0
        for idref in spine_idrefs:
            raise_if_cancelled(cancel_check, READING_CANCELLED)
            item = manifest.get(idref)
            if item is None:
                continue
            href, media_type, _props = item
            if not _is_content_doc(media_type, href):
                continue
            entry = _resolve(opf_dir, href)
            if entry not in names or _is_boilerplate_name(entry):
                continue
            in_zone = _in_matter_zone(spine_positions.get(entry, -1), len(spine_positions))
            if in_zone and entry in matter:
                matter_skipped += 1
                continue
            try:
                raw = _read_member_cancellable(zf, entry, epub_path, cancel_check)
            except OperationCancelled:
                raise
            except SetupError as exc:
                # Mine-what-you-can: one oversized chapter degrades to a
                # warning, unlike the structural members (container/OPF/
                # encryption) whose oversize aborts like the DRM gate.
                logger.debug(
                    "Ignored failure during spine document read of %s: %s: %s",
                    entry,
                    type(exc).__name__,
                    exc,
                )
                _warn_once(doc.warnings, f"Skipped oversized spine document '{entry}'.")
                continue
            except _OPTIONAL_MEMBER_ERRORS as exc:
                logger.debug(
                    "Ignored failure during spine document read of %s: %s: %s",
                    entry,
                    type(exc).__name__,
                    exc,
                )
                _warn_once(doc.warnings, f"Skipped damaged spine document '{entry}'.")
                continue
            account_member(raw)
            body, is_cover = _parse_content(raw)
            if body is None or is_cover:
                continue
            if in_zone and _is_matter_typed(body):
                matter_skipped += 1
                continue
            ruby: list[list[tuple[str, str]]] = []
            paragraphs, gaiji = _walk_body(body, cancel_check=cancel_check, line_join=_line_join(rules), ruby_out=ruby)
            gaiji_total += gaiji
            label = chapter_map.get(entry, f"ch.{content_i}")
            content_i += 1
            for para, para_ruby in zip(paragraphs, ruby, strict=True):
                raise_if_cancelled(cancel_check, READING_CANCELLED)
                for sentence in split_sentences(para, rules=rules):
                    raise_if_cancelled(cancel_check, READING_CANCELLED)
                    doc.units.append(
                        ReadingUnit(
                            text=sentence,
                            index=index,
                            location_label=label,
                            image_ref=cover_ref,
                            ruby=tuple(dict.fromkeys(pair for pair in para_ruby if pair[0] in sentence)),
                        )
                    )
                    index += 1

        if gaiji_total:
            doc.warnings.append(f"Skipped {gaiji_total} inline image(s) (gaiji) that carried no text.")
    log_summary(
        logger,
        "EPUB parse",
        file=epub_path,
        chapters=content_i,
        units=index,
        chars=sum(len(unit.text) for unit in doc.units),
        skipped=len(spine_idrefs) - content_i,
        gaiji=gaiji_total,
        matter=matter_skipped,
    )
    return doc


# --------------------------------------------------------------------------- #
# XML / element helpers
# --------------------------------------------------------------------------- #


def _local(el) -> str:
    """Namespace-stripped, lowercased tag name; ``""`` for comments/PIs."""
    tag = el.tag
    if not isinstance(tag, str):
        return ""
    return str(etree.QName(tag).localname).lower()


def _parse_xml(data: bytes):
    """Recovering, network-free XML parse; ``None`` if nothing usable comes out."""
    parser = etree.XMLParser(recover=True, resolve_entities=False, load_dtd=False, no_network=True)
    try:
        return etree.fromstring(data, parser)
    except etree.XMLSyntaxError as exc:
        # DEBUG: a recovering parser that still gives up means the member is
        # not XML at all, and the caller only reports "no usable body".
        logger.debug(
            "Ignored failure during EPUB XML parse of %d bytes: %s: %s",
            len(data),
            type(exc).__name__,
            exc,
        )
        return None


def _epub_type_tokens(el) -> set[str]:
    for attr in _EPUB_TYPE_ATTRS:
        val = el.get(attr)
        if val:
            return set(val.split())
    return set()


def _resolve(base_dir: str, href: str) -> str:
    """Resolve a manifest/nav href to a normalized (posix) zip entry name."""
    href = unquote(href.split("#", 1)[0])
    joined = posixpath.join(base_dir, href) if base_dir else href
    return posixpath.normpath(joined)


# --------------------------------------------------------------------------- #
# Encryption / DRM gate
# --------------------------------------------------------------------------- #


def _check_encryption(
    zf: zipfile.ZipFile,
    names: set[str],
    epub_path: Path,
    manifest: dict[str, tuple[str, str | None, list[str]]],
    opf_dir: str,
    account_member: _AccountMember,
    cancel_check: _CancelCheck | None,
) -> None:
    if _ENCRYPTION_PATH not in names:
        return
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
    try:
        encryption_raw = _read_member_cancellable(zf, _ENCRYPTION_PATH, epub_path, cancel_check)
    except _OPTIONAL_MEMBER_ERRORS as exc:
        raise SetupError(_invalid_epub_msg(epub_path, "META-INF/encryption.xml is unreadable")) from exc
    account_member(encryption_raw)
    try:
        root = etree.fromstring(encryption_raw, parser)
    except etree.XMLSyntaxError as exc:
        logger.debug(
            "EPUB encryption metadata parse failed: file=%s error=%s detail=%s",
            epub_path,
            type(exc).__name__,
            exc,
        )
        _reject_drm(epub_path, "malformed_encryption_metadata")
    found_encrypted_data = False
    for enc in root.iter():
        raise_if_cancelled(cancel_check, READING_CANCELLED)
        if _local(enc) != "encrypteddata":
            continue
        found_encrypted_data = True
        algorithm = None
        uri = None
        for sub in enc.iter():
            name = _local(sub)
            if name == "encryptionmethod" and algorithm is None:
                algorithm = sub.get("Algorithm")
            elif name == "cipherreference" and uri is None:
                uri = sub.get("URI")
        if (
            algorithm in _FONT_OBFUSCATION_ALGS
            and uri
            and _is_manifest_font(
                uri,
                names,
                manifest,
                opf_dir,
                cancel_check,
            )
        ):
            continue
        _reject_drm(epub_path, "unsupported_encryption")
    if not found_encrypted_data:
        _reject_drm(epub_path, "missing_encrypted_data")


def _is_manifest_font(
    uri: str,
    names: set[str],
    manifest: dict[str, tuple[str, str | None, list[str]]],
    opf_dir: str,
    cancel_check: _CancelCheck | None,
) -> bool:
    try:
        parsed = urlsplit(uri)
    except ValueError as exc:
        # DEBUG: an unsplittable @font-face URI is simply not a manifest font;
        # the URI itself is what says whether the stylesheet is malformed.
        logger.debug(
            "Ignored failure during font URI split of %r: %s: %s",
            uri,
            type(exc).__name__,
            exc,
        )
        return False
    if parsed.scheme or parsed.netloc or not parsed.path:
        return False
    target = _resolve("", parsed.path)
    if target not in names:
        return False
    matches: list[tuple[str, str | None, list[str]]] = []
    for item in manifest.values():
        raise_if_cancelled(cancel_check, READING_CANCELLED)
        if _resolve(opf_dir, item[0]) == target:
            matches.append(item)
    if len(matches) != 1:
        return False
    media_type = matches[0][1]
    return media_type is not None and media_type.lower() in _FONT_MEDIA_TYPES


def _reject_drm(epub_path: Path, reason: str) -> None:
    """Log one reason token, then reject unsupported content encryption."""
    log_summary(
        logger,
        "EPUB rejected",
        level=logging.WARNING,
        file=epub_path,
        reason=reason,
    )
    raise SetupError(f"'{epub_path.name}' is DRM-protected and cannot be mined.")


# --------------------------------------------------------------------------- #
# Container → OPF path
# --------------------------------------------------------------------------- #


def _find_opf_path(
    zf: zipfile.ZipFile,
    names: set[str],
    epub_path: Path,
    account_member: _AccountMember,
    cancel_check: _CancelCheck | None,
) -> str:
    if _CONTAINER_PATH not in names:
        raise SetupError(_invalid_epub_msg(epub_path, "META-INF/container.xml is missing"))
    try:
        container_raw = _read_member_cancellable(zf, _CONTAINER_PATH, epub_path, cancel_check)
    except _OPTIONAL_MEMBER_ERRORS as exc:
        raise SetupError(_invalid_epub_msg(epub_path, "META-INF/container.xml is unreadable")) from exc
    account_member(container_raw)
    root = _parse_xml(container_raw)
    fallback = None
    if root is not None:
        for el in root.iter():
            raise_if_cancelled(cancel_check, READING_CANCELLED)
            if _local(el) != "rootfile":
                continue
            full_path = el.get("full-path")
            if not full_path:
                continue
            if el.get("media-type") == _OPF_MEDIA_TYPE:
                return str(full_path)
            if fallback is None:
                fallback = full_path
    if fallback is not None:
        return str(fallback)
    raise SetupError(_invalid_epub_msg(epub_path, "no OPF package is declared"))


# --------------------------------------------------------------------------- #
# OPF (manifest / spine / metadata)
# --------------------------------------------------------------------------- #


def _parse_opf(
    root,
    *,
    cancel_check: _CancelCheck | None = None,
) -> tuple[dict[str, tuple[str, str | None, list[str]]], list[str], str | None, str | None, str | None]:
    manifest: dict[str, tuple[str, str | None, list[str]]] = {}
    spine_idrefs: list[str] = []
    spine_toc: str | None = None
    cover_meta_id: str | None = None
    title: str | None = None

    for el in root.iter():
        raise_if_cancelled(cancel_check, READING_CANCELLED)
        name = _local(el)
        if name == "item":
            item_id = el.get("id")
            href = el.get("href")
            if item_id and href:
                properties = (el.get("properties") or "").split()
                manifest[item_id] = (href, el.get("media-type"), properties)
        elif name == "spine":
            spine_toc = el.get("toc")
        elif name == "itemref":
            if (el.get("linear") or "").lower() == "no":
                continue
            idref = el.get("idref")
            if idref:
                spine_idrefs.append(idref)
        elif name == "title" and title is None:
            text = "".join(el.itertext()).strip()
            title = text or None
        elif name == "meta" and el.get("name") == "cover" and cover_meta_id is None:
            cover_meta_id = el.get("content")

    return manifest, spine_idrefs, spine_toc, cover_meta_id, title


# --------------------------------------------------------------------------- #
# Cover
# --------------------------------------------------------------------------- #


def _find_cover(
    zf: zipfile.ZipFile,
    names: set[str],
    manifest: dict[str, tuple[str, str | None, list[str]]],
    cover_meta_id: str | None,
    opf_dir: str,
    epub_path: Path,
    cancel_check: _CancelCheck | None,
) -> tuple[ImageRef | None, str | None]:
    cover_href = None
    for href, _mt, props in manifest.values():
        raise_if_cancelled(cancel_check, READING_CANCELLED)
        if "cover-image" in props:
            cover_href = href
            break
    if cover_href is None and cover_meta_id:
        item = manifest.get(cover_meta_id)
        if item is not None:
            cover_href = item[0]
    if not cover_href:
        return None, None

    entry = _resolve(opf_dir, cover_href)
    header = b""
    if entry in names:
        try:
            raise_if_cancelled(cancel_check, READING_CANCELLED)
            with zf.open(entry) as fp:
                header = fp.read(16)  # fixed-size peek: bomb-safe, never decoded
            raise_if_cancelled(cancel_check, READING_CANCELLED)
        except _OPTIONAL_MEMBER_ERRORS as exc:
            # DEBUG: the book still mines, without a cover; the caller's warning
            # cannot say whether the member was damaged or simply not an image.
            logger.debug(
                "Ignored failure during cover image peek of %s: %s: %s",
                entry,
                type(exc).__name__,
                exc,
            )
            header = b""
    if _is_image_magic(header):
        return ImageRef(epub_path, entry), None
    return None, (
        f"Cover image '{posixpath.basename(entry)}' is unreadable or not a supported image; "
        "the book will be mined without a cover."
    )


def _is_image_magic(header: bytes) -> bool:
    if header.startswith(b"\xff\xd8\xff"):  # JPEG
        return True
    if header.startswith(b"\x89PNG\r\n\x1a\n"):  # PNG
        return True
    if header[:6] in (b"GIF87a", b"GIF89a"):  # GIF
        return True
    return header[:4] == b"RIFF" and header[8:12] == b"WEBP"  # WebP


# --------------------------------------------------------------------------- #
# Spine XHTML → paragraphs
# --------------------------------------------------------------------------- #


def _is_content_doc(media_type: str | None, href: str) -> bool:
    if media_type in _CONTENT_MEDIA_TYPES:
        return True
    if media_type:
        return False
    return href.split("#", 1)[0].lower().endswith(_CONTENT_EXTS)


def _is_boilerplate_name(entry: str) -> bool:
    """True for front/back-matter spine files (cover/toc/colophon/caution, ``p-ad-*``).

    Matches whole delimiter-split tokens, never raw substrings, so real chapters
    like ``protocol.xhtml`` (contains "toc") or ``discover-chapter.xhtml``
    (contains "cover") are not mistaken for boilerplate.
    """
    stem = posixpath.basename(entry).rsplit(".", 1)[0].lower()
    if stem.startswith("p-ad-"):
        return True
    tokens = set(_STEM_DELIMITERS.split(stem))
    return bool(tokens & _BOILERPLATE_TOKENS)


def _guide_matter(root, opf_dir: str, cancel_check: _CancelCheck | None) -> set[str]:
    """Spine entries the EPUB2 ``<guide>`` types as front/back matter (TS-08)."""
    found: set[str] = set()
    for el in root.iter():
        raise_if_cancelled(cancel_check, READING_CANCELLED)
        if _local(el) != "reference":
            continue
        href = el.get("href")
        if href and _names_whole_document(href) and (el.get("type") or "").strip().lower() in _MATTER_TYPES:
            found.add(_resolve(opf_dir, href))
    return found


def _names_whole_document(href: str) -> bool:
    """True when ``href`` has no ``#fragment``, so it types the whole file.

    ``main.xhtml#toc`` names a block inside a file (EPUB2 conversions point the
    guide's ``toc`` at an inline contents list in the first chapter's file);
    ``_resolve`` drops the fragment, so letting it through would skip the
    whole chapter.
    """
    return not href.partition("#")[2]


def _content_positions(
    spine_idrefs: list[str],
    manifest: dict[str, tuple[str, str | None, list[str]]],
    opf_dir: str,
) -> dict[str, int]:
    """Each content document's first position among the spine's content documents."""
    positions: dict[str, int] = {}
    for idref in spine_idrefs:
        item = manifest.get(idref)
        if item is None or not _is_content_doc(item[1], item[0]):
            continue
        positions.setdefault(_resolve(opf_dir, item[0]), len(positions))
    return positions


def _in_matter_zone(position: int, total: int) -> bool:
    """Whether a spine position lies in the first or last :data:`_MATTER_ZONE` of the book.

    A book of one content document has no zone: its only document is the work.
    """
    if total < 2 or position < 0:
        return False
    edge = max(1, math.ceil(total * _MATTER_ZONE))
    return position < edge or position >= total - edge


# A nav document's companions to its ``toc`` nav: they never mark a file on
# their own, but they do not make a nav document the work either.
_NAV_COMPANION_TYPES = frozenset({"landmarks", "page-list"})


def _is_matter_typed(body) -> bool:
    """True when the body, or every section it holds, is typed as front/back matter.

    A file that opens with a typed title page and goes on with an untyped
    chapter section is the work, so only a body made entirely of typed
    ``section``/``div``/``nav`` children, at least one of them matter, counts.
    """
    if _epub_type_tokens(body) & _MATTER_TYPES:
        return True
    children = [child for child in body if _local(child)]
    if not children:
        return False
    found = False
    for child in children:
        if _local(child) not in ("section", "div", "nav"):
            return False
        tokens = _epub_type_tokens(child)
        if tokens & _MATTER_TYPES:
            found = True
        elif not tokens & _NAV_COMPANION_TYPES:
            return False
    return found


def _find_body(root):
    if root is None:
        return None
    if _local(root) == "body":
        return root
    for el in root.iter():
        if _local(el) == "body":
            return el
    return None


def _is_cover_typed(root, body) -> bool:
    for el in (root, body):
        if el is not None and "cover" in _epub_type_tokens(el):
            return True
    if body is not None:
        for child in body:
            if _local(child) and "cover" in _epub_type_tokens(child):
                return True
    return False


def _parse_content(raw: bytes):
    """Return ``(body_element_or_None, is_cover_typed)`` for one spine file."""
    root = _parse_xml(raw)
    body = _find_body(root)
    if body is None:
        try:
            root = html.document_fromstring(raw)
        except (etree.ParserError, etree.XMLSyntaxError, ValueError) as exc:
            # DEBUG: last rung of the parse ladder, so this chapter contributes
            # nothing; without it a book that mines zero words has no trace.
            logger.debug(
                "Ignored failure during EPUB HTML fallback parse of %d bytes: %s: %s",
                len(raw),
                type(exc).__name__,
                exc,
            )
            root = None
        body = _find_body(root)
    return body, _is_cover_typed(root, body)


def _ruby_pair(ruby) -> tuple[str, str] | None:
    """``(base, reading)`` of one ``<ruby>``: its base text and its ``<rt>`` text, both stripped.

    Group ruby (``<ruby>漢字<rt>かんじ</rt></ruby>``) and per-character ruby
    (``<ruby>漢<rt>かん</rt>字<rt>じ</rt></ruby>``) both give ``("漢字",
    "かんじ")``; ``<rp>`` fallback parentheses are not part of either. Raw and
    language-neutral: whether the reading is usable is the parser's call.
    """
    base: list[str] = []
    reading: list[str] = []

    def collect(node) -> None:
        name = _local(node)
        if name == "rt":
            reading.append("".join(node.itertext()))
            return
        if not name or name in _SKIP_TAGS:
            return
        if node.text:
            base.append(node.text)
        for child in node:
            collect(child)
            if child.tail:
                base.append(child.tail)

    collect(ruby)
    base_text = "".join(base).strip()
    reading_text = "".join(reading).strip()
    return (base_text, reading_text) if base_text and reading_text else None


def _walk_body(
    body,
    *,
    cancel_check: _CancelCheck | None = None,
    line_join: str = "",
    ruby_out: list[list[tuple[str, str]]] | None = None,
) -> tuple[list[str], int]:
    """Depth-first text walk → (paragraphs, gaiji-image count).

    Ruby/script/style subtrees are skipped; ``<img>`` counts toward gaiji and
    contributes no text; a paragraph flushes on a block close or ``<br>``, then
    has its leading whitespace (incl. U+3000) stripped and empties dropped.
    ``line_join`` replaces each source line wrap inside a paragraph
    (:func:`_line_join`). With ``ruby_out``, one list of :func:`_ruby_pair`
    pairs is appended per kept paragraph, in step with the paragraphs (SK-31).
    """
    paragraphs: list[str] = []
    buf: list[str] = []
    hints: list[tuple[str, str]] = []
    gaiji = 0

    def flush() -> None:
        if not buf:
            hints.clear()
            return
        text = _INTERNAL_LINEBREAK.sub(line_join, "".join(buf)).lstrip()
        buf.clear()
        if text:
            paragraphs.append(text)
            if ruby_out is not None:
                ruby_out.append(list(hints))
        hints.clear()

    def visit(el) -> None:
        nonlocal gaiji
        raise_if_cancelled(cancel_check, READING_CANCELLED)
        name = _local(el)
        if not name or name in _SKIP_TAGS:
            return  # comment/PI or skipped subtree — tail handled by the caller
        if name == "br":
            flush()
            return
        if name == "img":
            gaiji += 1
            return
        if name == "ruby" and ruby_out is not None:
            pair = _ruby_pair(el)
            if pair is not None:
                hints.append(pair)
        if el.text:
            buf.append(el.text)
        for child in el:
            visit(child)
            if child.tail:
                buf.append(child.tail)
        if name in _BLOCK_TAGS:
            flush()

    visit(body)
    flush()  # trailing inline text after the last block
    return paragraphs, gaiji


# --------------------------------------------------------------------------- #
# Chapters (nav / NCX)
# --------------------------------------------------------------------------- #


def _load_chapters(
    zf: zipfile.ZipFile,
    names: set[str],
    manifest: dict[str, tuple[str, str | None, list[str]]],
    spine_toc: str | None,
    opf_dir: str,
    warnings: list[str],
    account_member: _AccountMember,
    cancel_check: _CancelCheck | None,
) -> tuple[dict[str, str], set[str]]:
    """Chapter labels by spine entry, and the EPUB3 landmarks' matter entries (TS-08)."""
    entries: list[tuple[str, str]] = []
    landmarks: set[str] = set()
    nav_href = None
    for href, _mt, props in manifest.values():
        raise_if_cancelled(cancel_check, READING_CANCELLED)
        if "nav" in props:
            nav_href = href
            break
    if nav_href:
        entries, landmarks = _parse_nav(zf, names, opf_dir, nav_href, warnings, account_member, cancel_check)
    if not entries and spine_toc:
        item = manifest.get(spine_toc)
        if item is not None:
            entries = _parse_ncx(
                zf,
                names,
                opf_dir,
                item[0],
                warnings,
                account_member,
                cancel_check,
            )

    usable = [(t, lbl) for (t, lbl) in entries if lbl and lbl not in _BOILERPLATE_LABELS]
    if len(usable) < 2:
        return {}, landmarks
    chapter_map: dict[str, str] = {}
    for target, label in usable:
        chapter_map.setdefault(target, label)
    return chapter_map, landmarks


def _parse_nav(
    zf: zipfile.ZipFile,
    names: set[str],
    opf_dir: str,
    nav_href: str,
    warnings: list[str],
    account_member: _AccountMember,
    cancel_check: _CancelCheck | None,
) -> tuple[list[tuple[str, str]], set[str]]:
    """The toc nav's ``(target, label)`` entries, and the landmarks nav's matter targets (TS-08)."""
    nav_entry = _resolve(opf_dir, nav_href)
    if nav_entry not in names:
        return [], set()
    try:
        raw = _read_member_cancellable(zf, nav_entry, Path(nav_entry), cancel_check)
    except OperationCancelled:
        raise
    except (SetupError, *_OPTIONAL_MEMBER_ERRORS) as exc:
        logger.debug(
            "Ignored failure during navigation document read of %s: %s: %s",
            nav_entry,
            type(exc).__name__,
            exc,
        )
        _warn_once(warnings, f"Skipped damaged navigation document '{nav_entry}'.")
        return [], set()  # oversized nav → chapter labels fall back to spine index
    account_member(raw)
    root = _parse_xml(raw)
    if root is None:
        return [], set()
    nav_dir = posixpath.dirname(nav_entry)
    navs = [el for el in root.iter() if _local(el) == "nav"]
    landmarks: set[str] = set()
    for nav in navs:
        if "landmarks" not in _epub_type_tokens(nav):
            continue
        for a in nav.iter():
            raise_if_cancelled(cancel_check, READING_CANCELLED)
            href = a.get("href") if _local(a) == "a" else None
            if href and _names_whole_document(href) and _epub_type_tokens(a) & _MATTER_TYPES:
                landmarks.add(_resolve(nav_dir, href))
    chosen = next((nv for nv in navs if "toc" in _epub_type_tokens(nv)), None)
    if chosen is None:
        chosen = next((nv for nv in navs if nv.get("id") == "toc"), None)
    if chosen is None and navs:
        chosen = navs[0]
    if chosen is None:
        return [], landmarks
    out: list[tuple[str, str]] = []
    for a in chosen.iter():
        raise_if_cancelled(cancel_check, READING_CANCELLED)
        if _local(a) != "a":
            continue
        href = a.get("href")
        if not href:
            continue
        label = "".join(a.itertext()).strip()
        out.append((_resolve(nav_dir, href), label))
    return out, landmarks


def _parse_ncx(
    zf: zipfile.ZipFile,
    names: set[str],
    opf_dir: str,
    ncx_href: str,
    warnings: list[str],
    account_member: _AccountMember,
    cancel_check: _CancelCheck | None,
) -> list[tuple[str, str]]:
    ncx_entry = _resolve(opf_dir, ncx_href)
    if ncx_entry not in names:
        return []
    try:
        raw = _read_member_cancellable(zf, ncx_entry, Path(ncx_entry), cancel_check)
    except OperationCancelled:
        raise
    except (SetupError, *_OPTIONAL_MEMBER_ERRORS) as exc:
        logger.debug(
            "Ignored failure during NCX document read of %s: %s: %s",
            ncx_entry,
            type(exc).__name__,
            exc,
        )
        _warn_once(warnings, f"Skipped damaged navigation document '{ncx_entry}'.")
        return []  # oversized NCX → chapter labels fall back to spine index
    account_member(raw)
    root = _parse_xml(raw)
    if root is None:
        return []
    ncx_dir = posixpath.dirname(ncx_entry)
    out: list[tuple[str, str]] = []
    for point in root.iter():
        raise_if_cancelled(cancel_check, READING_CANCELLED)
        if _local(point) != "navpoint":
            continue
        label = None
        src = None
        for sub in point.iter():
            name = _local(sub)
            if name == "text" and label is None:
                label = "".join(sub.itertext()).strip()
            elif name == "content" and src is None:
                src = sub.get("src")
        if src:
            out.append((_resolve(ncx_dir, src), label or ""))
    return out


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def _invalid_epub_msg(epub_path: Path, detail: str) -> str:
    return f"'{epub_path.name}' is not a valid EPUB: {detail}."
