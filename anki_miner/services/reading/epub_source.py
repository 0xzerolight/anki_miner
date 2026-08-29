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
6. Each spine XHTML → base text (ruby readings and ``<img>`` gaiji dropped),
   paragraph-split on block close / ``<br>`` → ``sentence_splitter`` → units.

Loading decodes no image bytes and never materializes a page — the one disk
touch beyond text is the bomb-safe cover peek. The single shared cover
:class:`ImageRef` rides on every unit (books put the cover on every card).
"""

from __future__ import annotations

import logging
import lzma
import posixpath
import re
import zipfile
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

from lxml import etree, html  # type: ignore[import-untyped]

from anki_miner.exceptions import OperationCancelled, SetupError
from anki_miner.models.reading import (
    ImageRef,
    ReadingDocument,
    ReadingSourceRef,
    ReadingUnit,
)
from anki_miner.services.dictionary.zip_safety import MAX_UNCOMPRESSED_BYTES, validate_zip_safe
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

_CONTENT_MEDIA_TYPES = frozenset({"application/xhtml+xml", "text/html"})
_CONTENT_EXTS = (".xhtml", ".html", ".htm")

# Pretty-printed XHTML wraps paragraph text across lines with indent; join those
# CJK line-wraps with "" (no space) while leaving internal U+3000 untouched.
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


def _raise_if_cancelled(cancel_check: _CancelCheck | None) -> None:
    if cancel_check is not None and cancel_check():
        raise OperationCancelled("Reading load cancelled")


def _read_member(zf: zipfile.ZipFile, entry: str, epub_path: Path) -> bytes:
    """Read one zip member with a decompressed-size cap.

    Declared-size check first, then a bounded read of ``cap + 1`` bytes so an
    archive whose central directory under-declares the size cannot balloon
    memory anyway (same belt-and-suspenders as the Yomitan importer's
    ``_peek_zip_title_revision``). Raises :class:`SetupError` over the cap;
    callers decide whether that aborts (structural members) or soft-degrades
    (spine content, nav/NCX).
    """
    info = zf.getinfo(entry)
    if info.file_size > _MAX_MEMBER_BYTES:
        raise SetupError(
            f"'{epub_path.name}': member '{entry}' declares {info.file_size:,} bytes "
            f"(cap {_MAX_MEMBER_BYTES:,}); refusing to read."
        )
    with zf.open(entry) as fp:
        data = fp.read(_MAX_MEMBER_BYTES + 1)
    if len(data) > _MAX_MEMBER_BYTES:
        raise SetupError(f"'{epub_path.name}': member '{entry}' exceeds the {_MAX_MEMBER_BYTES:,}-byte cap.")
    return data


def _read_member_cancellable(
    zf: zipfile.ZipFile,
    entry: str,
    epub_path: Path,
    cancel_check: _CancelCheck | None,
) -> bytes:
    """Read one member with cancellation checks on both sides of the I/O."""
    _raise_if_cancelled(cancel_check)
    data = _read_member(zf, entry, epub_path)
    _raise_if_cancelled(cancel_check)
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
    _raise_if_cancelled(cancel_check)
    assert ref.path is not None
    epub_path = ref.path
    try:
        zf = zipfile.ZipFile(epub_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise SetupError(_invalid_epub_msg(epub_path, "the ZIP archive cannot be opened")) from exc
    with zf:
        _raise_if_cancelled(cancel_check)
        validate_zip_safe(zf, epub_path.parent)
        _raise_if_cancelled(cancel_check)
        total_member_bytes = 0

        def account_member(raw: bytes) -> None:
            nonlocal total_member_bytes
            _raise_if_cancelled(cancel_check)
            total_member_bytes += len(raw)
            if total_member_bytes > _MAX_TOTAL_MEMBER_BYTES:
                raise SetupError(
                    f"'{epub_path.name}': cumulative EPUB member data exceeds the {_MAX_TOTAL_MEMBER_BYTES:,}-byte cap."
                )

        names = set(zf.namelist())
        _raise_if_cancelled(cancel_check)
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

        chapter_map = _load_chapters(
            zf,
            names,
            manifest,
            spine_toc,
            opf_dir,
            doc.warnings,
            account_member,
            cancel_check,
        )

        index = 0
        content_i = 0
        gaiji_total = 0
        for idref in spine_idrefs:
            _raise_if_cancelled(cancel_check)
            item = manifest.get(idref)
            if item is None:
                continue
            href, media_type, _props = item
            if not _is_content_doc(media_type, href):
                continue
            entry = _resolve(opf_dir, href)
            if entry not in names or _is_boilerplate_name(entry):
                continue
            try:
                raw = _read_member_cancellable(zf, entry, epub_path, cancel_check)
            except OperationCancelled:
                raise
            except SetupError:
                # Mine-what-you-can: one oversized chapter degrades to a
                # warning, unlike the structural members (container/OPF/
                # encryption) whose oversize aborts like the DRM gate.
                _warn_once(doc.warnings, f"Skipped oversized spine document '{entry}'.")
                continue
            except _OPTIONAL_MEMBER_ERRORS:
                _warn_once(doc.warnings, f"Skipped damaged spine document '{entry}'.")
                continue
            account_member(raw)
            body, is_cover = _parse_content(raw)
            if body is None or is_cover:
                continue
            paragraphs, gaiji = _walk_body(body, cancel_check=cancel_check)
            gaiji_total += gaiji
            label = chapter_map.get(entry, f"ch.{content_i}")
            content_i += 1
            for para in paragraphs:
                _raise_if_cancelled(cancel_check)
                for sentence in split_sentences(para, rules=rules):
                    _raise_if_cancelled(cancel_check)
                    doc.units.append(
                        ReadingUnit(
                            text=sentence,
                            index=index,
                            location_label=label,
                            image_ref=cover_ref,
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
    except etree.XMLSyntaxError:
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
        _raise_if_cancelled(cancel_check)
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
    except ValueError:
        return False
    if parsed.scheme or parsed.netloc or not parsed.path:
        return False
    target = _resolve("", parsed.path)
    if target not in names:
        return False
    matches: list[tuple[str, str | None, list[str]]] = []
    for item in manifest.values():
        _raise_if_cancelled(cancel_check)
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
            _raise_if_cancelled(cancel_check)
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
        _raise_if_cancelled(cancel_check)
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
        _raise_if_cancelled(cancel_check)
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
            _raise_if_cancelled(cancel_check)
            with zf.open(entry) as fp:
                header = fp.read(16)  # fixed-size peek: bomb-safe, never decoded
            _raise_if_cancelled(cancel_check)
        except _OPTIONAL_MEMBER_ERRORS:
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
        except (etree.ParserError, etree.XMLSyntaxError, ValueError):
            root = None
        body = _find_body(root)
    return body, _is_cover_typed(root, body)


def _walk_body(
    body,
    *,
    cancel_check: _CancelCheck | None = None,
) -> tuple[list[str], int]:
    """Depth-first text walk → (paragraphs, gaiji-image count).

    Ruby/script/style subtrees are skipped; ``<img>`` counts toward gaiji and
    contributes no text; a paragraph flushes on a block close or ``<br>``, then
    has its leading whitespace (incl. U+3000) stripped and empties dropped.
    """
    paragraphs: list[str] = []
    buf: list[str] = []
    gaiji = 0

    def flush() -> None:
        if not buf:
            return
        text = _INTERNAL_LINEBREAK.sub("", "".join(buf)).lstrip()
        buf.clear()
        if text:
            paragraphs.append(text)

    def visit(el) -> None:
        nonlocal gaiji
        _raise_if_cancelled(cancel_check)
        name = _local(el)
        if not name or name in _SKIP_TAGS:
            return  # comment/PI or skipped subtree — tail handled by the caller
        if name == "br":
            flush()
            return
        if name == "img":
            gaiji += 1
            return
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
) -> dict[str, str]:
    entries: list[tuple[str, str]] = []
    nav_href = None
    for href, _mt, props in manifest.values():
        _raise_if_cancelled(cancel_check)
        if "nav" in props:
            nav_href = href
            break
    if nav_href:
        entries = _parse_nav(zf, names, opf_dir, nav_href, warnings, account_member, cancel_check)
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
        return {}
    chapter_map: dict[str, str] = {}
    for target, label in usable:
        chapter_map.setdefault(target, label)
    return chapter_map


def _parse_nav(
    zf: zipfile.ZipFile,
    names: set[str],
    opf_dir: str,
    nav_href: str,
    warnings: list[str],
    account_member: _AccountMember,
    cancel_check: _CancelCheck | None,
) -> list[tuple[str, str]]:
    nav_entry = _resolve(opf_dir, nav_href)
    if nav_entry not in names:
        return []
    try:
        raw = _read_member_cancellable(zf, nav_entry, Path(nav_entry), cancel_check)
    except OperationCancelled:
        raise
    except (SetupError, *_OPTIONAL_MEMBER_ERRORS):
        _warn_once(warnings, f"Skipped damaged navigation document '{nav_entry}'.")
        return []  # oversized nav → chapter labels fall back to spine index
    account_member(raw)
    root = _parse_xml(raw)
    if root is None:
        return []
    nav_dir = posixpath.dirname(nav_entry)
    navs = [el for el in root.iter() if _local(el) == "nav"]
    chosen = next((nv for nv in navs if "toc" in _epub_type_tokens(nv)), None)
    if chosen is None:
        chosen = next((nv for nv in navs if nv.get("id") == "toc"), None)
    if chosen is None and navs:
        chosen = navs[0]
    if chosen is None:
        return []
    out: list[tuple[str, str]] = []
    for a in chosen.iter():
        _raise_if_cancelled(cancel_check)
        if _local(a) != "a":
            continue
        href = a.get("href")
        if not href:
            continue
        label = "".join(a.itertext()).strip()
        out.append((_resolve(nav_dir, href), label))
    return out


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
    except (SetupError, *_OPTIONAL_MEMBER_ERRORS):
        _warn_once(warnings, f"Skipped damaged navigation document '{ncx_entry}'.")
        return []  # oversized NCX → chapter labels fall back to spine index
    account_member(raw)
    root = _parse_xml(raw)
    if root is None:
        return []
    ncx_dir = posixpath.dirname(ncx_entry)
    out: list[tuple[str, str]] = []
    for point in root.iter():
        _raise_if_cancelled(cancel_check)
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
