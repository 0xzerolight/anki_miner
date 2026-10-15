"""Write-time pass: Anki Miner's internal glossary markup -> Yomitan's Anki-export DOM.

Note types (Lapis, Senren, Kiku, Anki Miner Note) are written against the HTML
Yomitan puts into a card, so a card in that exact shape needs no stylesheet of
ours. Yomitan renders glossaries with classes, then ``normalizeHtml``
(ext/js/templates/anki-template-renderer.js) inlines the class rules of
ext/data/structured-content-style.json and strips every ``class`` and every
non-``data-sc-*`` data attribute; the grouped ``{glossary}`` template
(default-anki-field-templates.handlebars, ``compactTags`` on) wraps it as::

    <div style="text-align: left;" class="yomitan-glossary"><ol>
      <li data-dictionary="TITLE"><i>(tags, TITLE)</i> CONTENT</li>
      <li data-dictionary="TITLE"><i>(tags)</i> CONTENT</li> ...
      <style>scoped dictionary css</style>
    </ol></div>

The internal markup (``IndexedDictProvider._render``) stays for the readers that
parse it: render hooks, the media store, the curator preview. So this pass runs
LAST: in ``anki_note_builder.build_note`` (after ``upload_dict_media`` rewrote
the image srcs by class) and in the backfiller after its hooks. Deliberate
divergences: envelopes keep ``data-dictionary-id`` (dictionary CSS scope), and
a ``forms`` row keeps ``data-sc-content="forms"`` on its ``li`` so note-type
forms toggles apply.
"""

from __future__ import annotations

import html as html_lib
import re

from lxml import html as lxml_html  # type: ignore[import-untyped]

from anki_miner.services.dictionary.dict_css_scope import is_internal_glossary

# Yomitan ext/data/structured-content-style.json, the rules an element can match
# on an Anki card (pseudo-element, :root, sibling and interaction rules never
# match in Yomitan's CssStyleApplier either, so they are left out). Order kept.
_YOMITAN_CLASS_RULES: tuple[tuple[str, str], ...] = (
    (
        ".gloss-image-container",
        "display: inline-block; white-space: nowrap; max-width: 100%; max-height: 100vh; position: relative;"
        " vertical-align: top; line-height: 0; overflow: hidden; font-size: 1px;",
    ),
    (
        ".gloss-image-link",
        "cursor: inherit; display: inline-block; position: relative; line-height: 1; max-width: 100%;"
        " color: inherit;",
    ),
    (
        ".gloss-image-background",
        "--image: none; position: absolute; left: 0; top: 0; width: 100%; height: 100%;"
        " -webkit-mask-repeat: no-repeat; -webkit-mask-position: center center; -webkit-mask-mode: alpha;"
        " -webkit-mask-size: contain; -webkit-mask-image: var(--image); mask-repeat: no-repeat;"
        " mask-position: center center; mask-mode: alpha; mask-size: contain; mask-image: var(--image);"
        " background-color: currentColor;",
    ),
    (".gloss-image", "display: inline-block; vertical-align: top; object-fit: contain; border: none; outline: none;"),
    (
        ".gloss-image-link[data-has-aspect-ratio=true] .gloss-image",
        "position: absolute; left: 0; top: 0; width: 100%; height: 100%;",
    ),
    (
        ".gloss-image-link[data-image-rendering=pixelated] .gloss-image",
        "image-rendering: auto; image-rendering: -moz-crisp-edges; image-rendering: -webkit-optimize-contrast;"
        " image-rendering: pixelated; image-rendering: crisp-edges;",
    ),
    (
        ".gloss-image-link[data-image-rendering=pixelated] .gloss-image-background",
        "image-rendering: auto; image-rendering: -moz-crisp-edges; image-rendering: -webkit-optimize-contrast;"
        " image-rendering: pixelated; image-rendering: crisp-edges;",
    ),
    (
        ".gloss-image-link[data-image-rendering=crisp-edges] .gloss-image",
        "image-rendering: auto; image-rendering: -moz-crisp-edges; image-rendering: -webkit-optimize-contrast;"
        " image-rendering: crisp-edges;",
    ),
    (
        ".gloss-image-link[data-image-rendering=crisp-edges] .gloss-image-background",
        "image-rendering: auto; image-rendering: -moz-crisp-edges; image-rendering: -webkit-optimize-contrast;"
        " image-rendering: crisp-edges;",
    ),
    (
        ".gloss-image-link[data-has-aspect-ratio=true] .gloss-image-sizer",
        "display: inline-block; width: 0; vertical-align: top; font-size: 0;",
    ),
    (".gloss-image-link-text", "display: none;"),
    (".gloss-image-description", "display: block; white-space: pre-line;"),
    (
        ".gloss-image-link[data-appearance=monochrome] .gloss-image",
        "--shadow-settings: 0 0 0.01px var(--text-color); filter: grayscale(1) opacity(0.5)"
        " drop-shadow(var(--shadow-settings)) drop-shadow(var(--shadow-settings)) saturate(1000%)"
        " brightness(1000%); opacity: 0;",
    ),
    (".gloss-image-link[data-size-units=em] .gloss-image-container", "font-size: 1em;"),
    (".gloss-image-link[data-vertical-align=baseline]", "vertical-align: baseline;"),
    (".gloss-image-link[data-vertical-align=sub]", "vertical-align: sub;"),
    (".gloss-image-link[data-vertical-align=super]", "vertical-align: super;"),
    (".gloss-image-link[data-vertical-align=text-top]", "vertical-align: top;"),
    (".gloss-image-link[data-vertical-align=text-bottom]", "vertical-align: bottom;"),
    (".gloss-image-link[data-vertical-align=middle]", "vertical-align: middle;"),
    (".gloss-image-link[data-vertical-align=top]", "vertical-align: top;"),
    (".gloss-image-link[data-vertical-align=bottom]", "vertical-align: bottom;"),
    (".gloss-link-external-icon", "display: none;"),
    (".gloss-sc-table-container", "display: block;"),
    (".gloss-sc-table", "table-layout: auto; border-collapse: collapse;"),
    (".gloss-sc-thead", "font-weight: bold;"),
    (".gloss-sc-tfoot", "font-weight: bold;"),
    (".gloss-sc-th", "font-weight: bold;"),
    (
        ".gloss-sc-th",
        "border-style: solid; padding: 0.25em; vertical-align: top; border-width: 1px; border-color: currentColor;",
    ),
    (
        ".gloss-sc-td",
        "border-style: solid; padding: 0.25em; vertical-align: top; border-width: 1px; border-color: currentColor;",
    ),
    (".gloss-image-link:not([data-appearance=monochrome]) .gloss-image-background", "display: none;"),
)

_TOKEN_RE = re.compile(r"\.([\w-]+)|:not\(\[([\w-]+)=([^\]]+)\]\)|\[([\w-]+)=([^\]]+)\]")
#: XML-invalid control characters (tab, newline and carriage return are valid).
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_OPEN = '<div style="text-align: left;" class="yomitan-glossary"><ol>'
_CLOSE = "</ol></div>"


def _parse_compound(compound: str) -> list[tuple[str, str, str]]:
    """``.a[x=y]:not([z=w])`` -> [("class", "a", ""), ("attr", "x", "y"), ("not", "z", "w")]."""
    parts: list[tuple[str, str, str]] = []
    for m in _TOKEN_RE.finditer(compound):
        if m.group(1):
            parts.append(("class", m.group(1), ""))
        elif m.group(2):
            parts.append(("not", m.group(2), m.group(3)))
        else:
            parts.append(("attr", m.group(4), m.group(5)))
    return parts


_RULES = tuple(([_parse_compound(c) for c in sel.split()], decls) for sel, decls in _YOMITAN_CLASS_RULES)


def _classes(el) -> list[str]:
    return (el.get("class") or "").split() if isinstance(el.tag, str) else []


def _matches_compound(el, compound: list[tuple[str, str, str]]) -> bool:
    classes = _classes(el)
    for kind, name, value in compound:
        if kind == "class" and name not in classes:
            return False
        if kind == "attr" and el.get(name) != value:
            return False
        if kind == "not" and el.get(name) == value:
            return False
    return True


def _matches(el, chain: list[list[tuple[str, str, str]]]) -> bool:
    """Descendant-combinator match, right to left (all the rules need)."""
    if not _matches_compound(el, chain[-1]):
        return False
    remaining = chain[:-1]
    node = el.getparent()
    while remaining and node is not None:
        if _matches_compound(node, remaining[-1]):
            remaining = remaining[:-1]
        node = node.getparent()
    return not remaining


def _apply_yomitan_normalize(root) -> None:
    """``CssStyleApplier.applyClassStyles`` + ``normalizeHtml``'s dataset strip.

    Yomitan wraps every table in ``div.gloss-sc-table-container`` first. Matched
    declarations come first and the element's own inline style is appended
    after, so the dictionary's inline values win, as in Yomitan.
    """
    for el in [e for e in root.iter("table") if "gloss-sc-table" in _classes(e)]:
        parent = el.getparent()
        if parent is not None and "gloss-sc-table-container" not in _classes(parent):
            wrapper = lxml_html.Element("div", {"class": "gloss-sc-table-container"})
            parent.replace(el, wrapper)
            wrapper.append(el)
            wrapper.tail, el.tail = el.tail, None
    elements = [el for el in root.iter() if isinstance(el.tag, str)]
    decls = {id(el): " ".join(d for chain, d in _RULES if _matches(el, chain)) for el in elements if el.get("class")}
    for el in elements:
        if id(el) in decls:
            style = " ".join(s for s in (decls[id(el)], (el.get("style") or "").strip()) if s)
            del el.attrib["class"]
            if style:
                el.set("style", style)
            elif "style" in el.attrib:
                del el.attrib["style"]
        for key in list(el.attrib):
            if key.startswith("data-") and not key.startswith("data-sc"):
                del el.attrib[key]


def _rtl_example_bidi(root) -> None:
    """Right-to-left languages: each native example sentence takes the direction
    of its own first strong character (wty ar/fa/he glosses are English, their
    examples are not)."""
    for el in root.iter():
        if isinstance(el.tag, str) and el.get("data-sc-content") == "example-sentence-a":
            style = (el.get("style") or "").strip().rstrip(";")
            el.set("style", f"{style}; unicode-bidi: plaintext;" if style else "unicode-bidi: plaintext;")


def _text(text: str | None) -> str:
    return html_lib.escape(text or "", quote=False)


def _keep_tail(node, out: list[str]) -> None:
    """Keep ``node``'s tail text when the node itself is not carried over."""
    if (node.tail or "").strip():
        out.append(_text(node.tail))


def _outer(el) -> str:
    """``el`` serialized with its tail."""
    return str(lxml_html.tostring(el, encoding="unicode", with_tail=True))


def _inner_html(el) -> str:
    return _text(el.text) + "".join(_outer(child) for child in el)


def _is_synth_members_list(el) -> bool:
    """``render_glossary_entry``'s multi-member list: class + inline circle, nothing else."""
    return el.tag == "ul" and dict(el.attrib) == {"class": "gloss-sc-ul", "style": "list-style-type: circle"}


def _has_synth_members_list(content) -> bool:
    children = [c for c in content if isinstance(c.tag, str)]
    return (
        len(children) == 1
        and not (content.text or "").strip()
        and not (children[0].tail or "").strip()
        and _is_synth_members_list(children[0])
    )


def _wrap_member(el) -> str:
    """A structured-content member gets Yomitan's root ``<span>``; a text member stays bare."""
    inner = _inner_html(el)
    if any(isinstance(child.tag, str) and child.tag != "br" for child in el):
        return f"<span>{inner}</span>"
    return inner


def _content_html(content, synth: bool) -> str:
    if synth:
        members = next(c for c in content if isinstance(c.tag, str))
        return "<ul>" + "".join(f"<li>{_wrap_member(li)}</li>" for li in members if li.tag == "li") + "</ul>"
    return _wrap_member(content)


def _italic_tags(i_el, title: str) -> list[str]:
    """Tags from a legacy ``<i>(t1, t2, TITLE)</i>`` line (unstamped old cards)."""
    text = (i_el.text_content() or "").strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    if text == title:
        return []
    text = text.removesuffix(", " + title)
    return [t for t in text.split(", ") if t]


def _definition_tags(stamped: str | None, group_tags: list[str], position: int) -> list[str]:
    """``_render``'s compacted per-definition label; an unstamped old card shows
    its group's tags on the group's first definition only."""
    if stamped is None:
        return group_tags if position == 0 else []
    return [stamped] if stamped else []


def _export_definition(item, direction: str) -> tuple[str, list[str]]:
    """One internal ``li.gloss-item`` -> (exported content, leftover HTML).

    The content is the item's ``.gloss-content`` child (a ``<div>`` since
    v2.7.0, a ``<span>`` in v2.4.0-v2.6.7), or the whole item when there is
    none. Anything else in the item (hand edits) is returned as leftovers.
    """
    content = next((c for c in item if "gloss-content" in _classes(c)), None)
    leftovers: list[str] = []
    if content is None:
        content = item
    else:
        if (item.text or "").strip():
            leftovers.append(_text(item.text))
        leftovers.extend(_outer(c) for c in item if c is not content)
        if (content.tail or "").strip():
            leftovers.append(_text(content.tail))
    synth = _has_synth_members_list(content)
    _apply_yomitan_normalize(content)
    if direction == "rtl":
        _rtl_example_bidi(content)
    return _content_html(content, synth), leftovers


def _export_envelope(envelope, direction: str, items: list[str], leftovers: list[str]) -> None:
    """Append one dictionary's definitions to ``items`` (and hand edits to ``leftovers``).

    An envelope without an internal ``ul.gloss-list`` is already Yomitan-shaped
    (or hand-made): it is kept verbatim as one item.
    """
    if not any(child.tag == "ul" and "gloss-list" in _classes(child) for child in envelope):
        items.append(str(lxml_html.tostring(envelope, encoding="unicode", with_tail=False)))
        return
    title = envelope.get("data-dictionary") or ""
    dict_id = envelope.get("data-dictionary-id")
    attrs = f' data-dictionary="{html_lib.escape(title, quote=True)}"'
    if dict_id:
        attrs += f' data-dictionary-id="{html_lib.escape(dict_id, quote=True)}"'
    first = True
    group_tags: list[str] = []
    if (envelope.text or "").strip():
        leftovers.append(_text(envelope.text))
    for child in envelope:
        if not isinstance(child.tag, str):  # a comment: dropped, its tail kept
            _keep_tail(child, leftovers)
            continue
        if child.tag == "span" and "gloss-tag" in _classes(child):
            group_tags.append(child.text_content())
        elif child.tag == "i":
            group_tags.extend(_italic_tags(child, title))
        elif child.tag == "ul" and "gloss-list" in _classes(child):
            position = 0
            if (child.text or "").strip():
                leftovers.append(_text(child.text))
            for item in child:
                if not isinstance(item.tag, str):
                    _keep_tail(item, leftovers)
                    continue
                if item.tag != "li" or "gloss-item" not in _classes(item):
                    leftovers.append(_outer(item))
                    continue
                tags = _definition_tags(item.get("data-am-tags"), group_tags, position)
                position += 1
                if first:  # compactTags: the dictionary name opens each dictionary
                    tags = [*tags, title]
                    first = False
                content, item_leftovers = _export_definition(item, direction)
                leftovers.extend(item_leftovers)
                forms = ' data-sc-content="forms"' if item.get("data-sc-content") == "forms" else ""
                label = f"<i>({html_lib.escape(', '.join(tags), quote=False)})</i> " if tags else ""
                items.append(f"<li{attrs}{forms}>{label}{content}</li>")
                if (item.tail or "").strip():
                    leftovers.append(_text(item.tail))
            group_tags = []
        else:
            leftovers.append(_outer(child))
            continue
        if (child.tail or "").strip():
            leftovers.append(_text(child.tail))


def to_yomitan_export(field_html: str, *, direction: str = "ltr") -> str:
    """Return ``field_html`` in Yomitan's grouped ``{glossary}`` shape.

    Input without internal miner markup (``dict_css_scope.is_internal_glossary``)
    is returned unchanged, which makes the pass idempotent and leaves genuine
    Yomitan cards alone. Every ``<style>`` found is kept, deduplicated, trailing
    inside the ``<ol>``. Content the pass does not recognise (hand edits, text
    around the wrapper) is kept verbatim after it: the pass never drops card
    content.
    """
    if not is_internal_glossary(field_html):
        return field_html
    # lxml's setters reject XML-invalid control characters the HTML parser
    # accepts; they render as nothing, so they go before parsing.
    field_html = CONTROL_CHARS_RE.sub("", field_html)
    items: list[str] = []
    styles: list[str] = []
    leftovers: list[str] = []
    rest: list[str] = []
    for node in lxml_html.fragments_fromstring(field_html):
        if isinstance(node, str):
            rest.append(_text(node))
            continue
        for style in list(node.iter("style")):
            css = style.text or ""
            if css.strip() and css not in styles:
                styles.append(css)
            if style is not node:
                style.drop_tree()
        tail = _text(node.tail)
        if node.tag == "style":
            rest.append(tail)
            continue
        if "yomitan-glossary" in _classes(node) and node.find("ol") is not None:
            before = len(items)
            wrapper_leftovers: list[str] = []
            if (node.text or "").strip():
                wrapper_leftovers.append(_text(node.text))
            for child in node:
                if child.tag != "ol":
                    wrapper_leftovers.append(_outer(child))
                    continue
                if (child.text or "").strip():
                    wrapper_leftovers.append(_text(child.text))
                for li in child:
                    if isinstance(li.tag, str) and li.tag == "li" and li.get("data-dictionary") is not None:
                        _export_envelope(li, direction, items, wrapper_leftovers)
                        _keep_tail(li, wrapper_leftovers)
                    elif isinstance(li.tag, str):
                        wrapper_leftovers.append(_outer(li))
                    else:
                        _keep_tail(li, wrapper_leftovers)
                if (child.tail or "").strip():
                    wrapper_leftovers.append(_text(child.tail))
            if len(items) == before:  # nothing of ours in it: keep the wrapper as it was
                node.tail = None
                rest.append(_outer(node) + tail)
                continue
            leftovers.extend(wrapper_leftovers)
            rest.append(tail)
        else:
            node.tail = None
            rest.append(_outer(node) + tail)
    if not items:
        return field_html
    body = "".join(items) + "".join(f"<style>{css}</style>" for css in styles)
    return _OPEN + body + _CLOSE + "".join(leftovers) + "".join(rest)
