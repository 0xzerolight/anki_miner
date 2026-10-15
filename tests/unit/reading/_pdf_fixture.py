"""Build tiny text-layer PDFs for the TS-05 tests (no font program, no extra dependency).

The leading underscore keeps pytest from collecting it; tests import ``make_pdf``.
"""

from __future__ import annotations


def _cmap(code_points: list[int]) -> bytes:
    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<0000> <FFFF>",
        "endcodespacerange",
    ]
    for start in range(0, len(code_points), 100):
        chunk = code_points[start : start + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        lines.extend(f"<{cp:04X}> <{cp:04X}>" for cp in chunk)
        lines.append("endbfchar")
    lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
    return "\n".join(lines).encode("ascii")


def make_pdf(pages: list[list[str]]) -> bytes:
    """A PDF whose page ``i`` shows the lines ``pages[i]`` top to bottom.

    Text is drawn with a Type0 font under Identity-H, where each character's
    code is its own BMP code point and a ToUnicode CMap maps it back, so any
    script (Japanese included) round-trips through ``extract_text``. A page
    given as ``[]`` has no text at all, like a scanned page.
    """
    code_points = sorted({ord(ch) for page in pages for line in page for ch in line})
    if any(cp > 0xFFFF for cp in code_points):
        raise ValueError("make_pdf supports BMP characters only")
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    cmap_body = _cmap(code_points or [0x20])
    cmap_id = add(b"<< /Length %d >>\nstream\n" % len(cmap_body) + cmap_body + b"\nendstream")
    descendant_id = add(
        b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /TestFont "
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /DW 1000 >>"
    )
    font_id = add(
        b"<< /Type /Font /Subtype /Type0 /BaseFont /TestFont /Encoding /Identity-H "
        b"/DescendantFonts [%d 0 R] /ToUnicode %d 0 R >>" % (descendant_id, cmap_id)
    )
    pages_id_placeholder = len(objects) + 1
    objects.append(b"")  # the /Pages node, filled in once the kids exist
    kids: list[int] = []
    for lines in pages:
        ops = [b"BT", b"/F1 12 Tf", b"14 TL", b"72 720 Td"]
        for line in lines:
            hexed = "".join(f"{ord(ch):04X}" for ch in line).encode("ascii")
            ops.append(b"<" + hexed + b"> Tj T*")
        ops.append(b"ET")
        content = b"\n".join(ops)
        content_id = add(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        kids.append(
            add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
                % (pages_id_placeholder, font_id, content_id)
            )
        )
    objects[pages_id_placeholder - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % kid for kid in kids),
        len(kids),
    )
    catalog_id = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id_placeholder)

    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        catalog_id,
        xref_at,
    )
    return bytes(out)
