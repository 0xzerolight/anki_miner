"""Load one ``.epub`` novel into a :class:`ReadingDocument`.

Pure ``zipfile`` + ``lxml`` — no ``ebooklib`` (AGPL, and it adds nothing over
walking the container/OPF ourselves). The flow mirrors the reader spec:

1. ``META-INF/encryption.xml`` — content encryption (anything other than the
   two IDPF/Adobe font-obfuscation algorithms, or a cipher aimed at a non-font
   resource) is DRM: abort with a clear error naming the file. Font obfuscation
   is benign and the book mines normally.
2. ``META-INF/container.xml`` → the OPF package path.
3. OPF → manifest (id → href/media-type/properties), ordered spine (``linear``
   ``no`` skipped), ``dc:title``/``dc:creator``.
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

import posixpath
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote

from lxml import etree, html  # type: ignore[import-untyped]

from anki_miner.exceptions import SetupError
from anki_miner.services.reading.models import (
    ImageRef,
    ReadingDocument,
    ReadingSourceRef,
    ReadingUnit,
)
from anki_miner.services.reading.sentence_splitter import split_sentences

_CONTAINER_PATH = "META-INF/container.xml"
_ENCRYPTION_PATH = "META-INF/encryption.xml"
_OPF_MEDIA_TYPE = "application/oebps-package+xml"

# Namespaced attribute names that survive both the XML and the lxml.html parser.
_EPUB_TYPE_ATTRS = ("{http://www.idpf.org/2007/ops}type", "epub:type")

# Encryption algorithms that merely obfuscate embedded fonts — safe to mine.
_FONT_OBFUSCATION_ALGS = frozenset({"http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"})
_FONT_EXTS = (".otf", ".ttf", ".ttc", ".woff", ".woff2", ".eot", ".dfont")

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


def load(ref: ReadingSourceRef) -> ReadingDocument:
    """Load ``ref.path`` (an ``.epub``) into a book :class:`ReadingDocument`.

    Raises :class:`SetupError` for DRM-protected or structurally invalid files;
    soft problems (unreadable cover, gaiji images) become ``warnings`` and the
    book still mines.
    """
    epub_path = ref.path
    with zipfile.ZipFile(epub_path) as zf:
        names = set(zf.namelist())
        _check_encryption(zf, names, epub_path)

        opf_path = _find_opf_path(zf, names, epub_path)
        opf_dir = posixpath.dirname(opf_path)
        opf_root = _parse_xml(zf.read(opf_path))
        if opf_root is None:
            raise SetupError(_invalid_epub_msg(epub_path, "the OPF package is unreadable"))
        manifest, spine_idrefs, spine_toc, cover_meta_id, title = _parse_opf(opf_root)

        doc_title = title or ref.title
        doc = ReadingDocument(title=doc_title, kind="book", series="Books", episode=doc_title)

        cover_ref, cover_warning = _find_cover(zf, names, manifest, cover_meta_id, opf_dir, epub_path)
        if cover_warning:
            doc.warnings.append(cover_warning)

        chapter_map = _load_chapters(zf, names, manifest, spine_toc, opf_dir)

        index = 0
        content_i = 0
        gaiji_total = 0
        for idref in spine_idrefs:
            item = manifest.get(idref)
            if item is None:
                continue
            href, media_type, _props = item
            if not _is_content_doc(media_type, href):
                continue
            entry = _resolve(opf_dir, href)
            if entry not in names or _is_boilerplate_name(entry):
                continue
            body, is_cover = _parse_content(zf.read(entry))
            if body is None or is_cover:
                continue
            paragraphs, gaiji = _walk_body(body)
            gaiji_total += gaiji
            label = chapter_map.get(entry, f"ch.{content_i}")
            content_i += 1
            for para in paragraphs:
                for sentence in split_sentences(para):
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
# 1. Encryption / DRM gate
# --------------------------------------------------------------------------- #


def _check_encryption(zf: zipfile.ZipFile, names: set[str], epub_path: Path) -> None:
    if _ENCRYPTION_PATH not in names:
        return
    root = _parse_xml(zf.read(_ENCRYPTION_PATH))
    if root is None:
        return
    for enc in root.iter():
        if _local(enc) != "encrypteddata":
            continue
        algorithm = None
        uri = None
        for sub in enc.iter():
            name = _local(sub)
            if name == "encryptionmethod" and algorithm is None:
                algorithm = sub.get("Algorithm")
            elif name == "cipherreference" and uri is None:
                uri = sub.get("URI")
        if algorithm in _FONT_OBFUSCATION_ALGS:
            continue
        if uri and unquote(uri).lower().endswith(_FONT_EXTS):
            continue
        raise SetupError(f"'{epub_path.name}' is DRM-protected and cannot be mined.")


# --------------------------------------------------------------------------- #
# 2. Container → OPF path
# --------------------------------------------------------------------------- #


def _find_opf_path(zf: zipfile.ZipFile, names: set[str], epub_path: Path) -> str:
    if _CONTAINER_PATH not in names:
        raise SetupError(_invalid_epub_msg(epub_path, "META-INF/container.xml is missing"))
    root = _parse_xml(zf.read(_CONTAINER_PATH))
    fallback = None
    if root is not None:
        for el in root.iter():
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
# 3. OPF (manifest / spine / metadata)
# --------------------------------------------------------------------------- #


def _parse_opf(
    root,
) -> tuple[dict[str, tuple[str, str | None, list[str]]], list[str], str | None, str | None, str | None]:
    manifest: dict[str, tuple[str, str | None, list[str]]] = {}
    spine_idrefs: list[str] = []
    spine_toc: str | None = None
    cover_meta_id: str | None = None
    title: str | None = None

    for el in root.iter():
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
# 4. Cover
# --------------------------------------------------------------------------- #


def _find_cover(
    zf: zipfile.ZipFile,
    names: set[str],
    manifest: dict[str, tuple[str, str | None, list[str]]],
    cover_meta_id: str | None,
    opf_dir: str,
    epub_path: Path,
) -> tuple[ImageRef | None, str | None]:
    cover_href = None
    for href, _mt, props in manifest.values():
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
            with zf.open(entry) as fp:
                header = fp.read(16)  # fixed-size peek: bomb-safe, never decoded
        except (KeyError, zipfile.BadZipFile):
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
# 5 + 6. Spine XHTML → paragraphs
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


def _walk_body(body) -> tuple[list[str], int]:
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
# 7. Chapters (nav / NCX)
# --------------------------------------------------------------------------- #


def _load_chapters(
    zf: zipfile.ZipFile,
    names: set[str],
    manifest: dict[str, tuple[str, str | None, list[str]]],
    spine_toc: str | None,
    opf_dir: str,
) -> dict[str, str]:
    entries: list[tuple[str, str]] = []
    nav_href = None
    for href, _mt, props in manifest.values():
        if "nav" in props:
            nav_href = href
            break
    if nav_href:
        entries = _parse_nav(zf, names, opf_dir, nav_href)
    if not entries and spine_toc:
        item = manifest.get(spine_toc)
        if item is not None:
            entries = _parse_ncx(zf, names, opf_dir, item[0])

    usable = [(t, lbl) for (t, lbl) in entries if lbl and lbl not in _BOILERPLATE_LABELS]
    if len(usable) < 2:
        return {}
    chapter_map: dict[str, str] = {}
    for target, label in usable:
        chapter_map.setdefault(target, label)
    return chapter_map


def _parse_nav(zf: zipfile.ZipFile, names: set[str], opf_dir: str, nav_href: str) -> list[tuple[str, str]]:
    nav_entry = _resolve(opf_dir, nav_href)
    if nav_entry not in names:
        return []
    root = _parse_xml(zf.read(nav_entry))
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
        if _local(a) != "a":
            continue
        href = a.get("href")
        if not href:
            continue
        label = "".join(a.itertext()).strip()
        out.append((_resolve(nav_dir, href), label))
    return out


def _parse_ncx(zf: zipfile.ZipFile, names: set[str], opf_dir: str, ncx_href: str) -> list[tuple[str, str]]:
    ncx_entry = _resolve(opf_dir, ncx_href)
    if ncx_entry not in names:
        return []
    root = _parse_xml(zf.read(ncx_entry))
    if root is None:
        return []
    ncx_dir = posixpath.dirname(ncx_entry)
    out: list[tuple[str, str]] = []
    for point in root.iter():
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
