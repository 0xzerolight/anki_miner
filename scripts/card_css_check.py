#!/usr/bin/env python3
"""Does the house glossary sheet beat a note type's own rules? Render and ask Chromium.

dd8dc96 fixed a house rule (``glossary.css``) that out-ranked Senren's own list
padding on specificity and gave every Senren card a blank line. This check builds
sample fields with the mining code (fixture dictionaries → import → DefinitionService
→ ``attach_card_style_block``), renders them through the pinned releases of Lapis,
Senren, Kiku (each definition style) and Anki Miner Note, plus stock Basic, the way
Anki's reviewer does, and reads every miner element's matched rules over CDP. It
lists each place a house declaration beats a note-type declaration of the same
property, or replaces a value the element would inherit from one. That list, plus
each render's page errors, must equal ``card_css_baseline.json``: every entry there
is a reviewed interaction, and a new one is a review decision, not noise.

    python -I scripts/card_css_check.py                    # the check (health.sh)
    python -I scripts/card_css_check.py --update-baseline  # after reviewing the diff
    python -I scripts/card_css_check.py --shots DIR        # + DIR/report.html

``--shots`` adds light/dark screenshots at 412 and 1024 px and the report-only
findings: note-type declarations that beat the house sheet, blank-line gaps,
list markers with no gutter, and samples the house sheet doesn't style at all.

Needs the ``card-css`` extra and ``python -m playwright install chromium``. Exit
codes: 0 matches the baseline, 1 drift or a harness failure, 3 a dependency is
missing (Playwright, its Chromium, or an uncached note type with no network).
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import html
import http.server
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
BASELINE = REPO / "scripts" / "card_css_baseline.json"
CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "anki-miner-dev" / "card-css"
DEPENDENCY_MISSING = 3


class HarnessError(Exception):
    """A render failed in a way that says nothing about the CSS; the run fails."""


class DependencyMissing(Exception):
    """Something the check needs isn't installed or reachable; health.sh reports SKIP."""


# ---------------------------------------------------------------------------
# Pinned note types: the release packages users install. Bumping a pin is a
# review: rerun, read the baseline diff, then --update-baseline.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class NoteTypePin:
    key: str
    url: str
    sha256: str
    model: str  # the note type's name inside the package


PINS = (
    NoteTypePin(
        "lapis",
        "https://github.com/donkuri/lapis/releases/download/1.7.0/Lapis.apkg",
        "85984f8c6f1263e2882c364bbbfdee4bce9e1e03f053a0a5b0ccc6cd90de0088",
        "Lapis",
    ),
    NoteTypePin(
        "senren",
        "https://github.com/BrenoAqua/Senren/releases/download/v5.1.0/Senren.v5.1.0.apkg",
        "319367e09af9dfd698c3e1a953d404151222d6680ecf6b6acc5d68499578e659",
        "Senren",
    ),
    NoteTypePin(
        "kiku",
        "https://github.com/youyoumu/kiku/releases/download/v2.1.0/Kiku_v2.1.0.apkg",
        "e6a3f1d37ee530e07c02ea7f6d64d98f5702e9691c2ccc512ea288b372cef045",
        "Kiku",
    ),
    NoteTypePin(
        "amn",
        "https://github.com/0xzerolight/anki_miner_note/releases/download/v1.0.2/Anki-Miner-Note-v1.0.2.apkg",
        "fb2e3424e06f4d06dcb64d3b59e678124a85c5106fe2f37c5e4020e6cdad0725",
        "Anki Miner Note",
    ),
)

# Anki's stock Basic note type.
BASIC = {
    "front": "{{Front}}",
    "back": "{{FrontSide}}\n\n<hr id=answer>\n\n{{Back}}",
    "css": ".card {\n    font-family: arial;\n    font-size: 20px;\n    text-align: center;\n"
    "    color: black;\n    background-color: white;\n}\n",
}

_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def _zstd_decompress(data: bytes) -> bytes:
    try:
        from compression import zstd  # type: ignore[import-not-found]  # Python 3.14+
    except ImportError:
        cli = shutil.which("zstd")
        if cli is None:
            raise DependencyMissing("a v3 .apkg needs Python 3.14+ or the zstd CLI") from None
        return subprocess.run([cli, "-dc"], input=data, capture_output=True, check=True).stdout
    return bytes(zstd.decompress(data))


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = buf[i]
        i += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, i


def _proto_fields(buf: bytes) -> list[tuple[int, int | bytes]]:
    """Top-level ``(field_number, value)`` pairs of one protobuf message."""
    fields: list[tuple[int, int | bytes]] = []
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        number, wire = key >> 3, key & 7
        value: int | bytes
        if wire == 0:
            value, i = _varint(buf, i)
        elif wire == 2:
            size, i = _varint(buf, i)
            value, i = buf[i : i + size], i + size
        elif wire == 1:
            value, i = buf[i : i + 8], i + 8
        elif wire == 5:
            value, i = buf[i : i + 4], i + 4
        else:
            raise ValueError(f"protobuf wire type {wire}")
        fields.append((number, value))
    return fields


def _proto_string(buf: bytes, number: int) -> str:
    for n, value in _proto_fields(buf):
        if n == number and isinstance(value, bytes):
            return value.decode("utf-8")
    return ""


def _read_note_type(db: sqlite3.Connection, model: str, *, v3: bool) -> dict[str, Any]:
    if v3:
        # Schema 18: config blobs are protobuf (Notetype.Config.css = 3,
        # Template.Config.q_format = 1 / a_format = 2).
        row = db.execute("select id, config from notetypes where name = ?", (model,)).fetchone()
        if row is None:
            raise RuntimeError(f"no note type {model!r} in the package")
        ntid, config = row
        template = db.execute("select config from templates where ntid = ? order by ord", (ntid,)).fetchone()[0]
        return {
            "front": _proto_string(template, 1),
            "back": _proto_string(template, 2),
            "css": _proto_string(config, 3),
        }
    models = json.loads(db.execute("select models from col").fetchone()[0])
    match = next((m for m in models.values() if m["name"] == model), None)
    if match is None:
        raise RuntimeError(f"no note type {model!r} in the package")
    return {"front": match["tmpls"][0]["qfmt"], "back": match["tmpls"][0]["afmt"], "css": match["css"]}


def _media_members(package: zipfile.ZipFile, *, v3: bool) -> dict[str, str]:
    """Zip member → media file name."""
    raw = package.read("media")
    if not v3:
        return dict(json.loads(raw))
    members: dict[str, str] = {}
    entries = [value for number, value in _proto_fields(_zstd_decompress(raw)) if number == 1]
    for index, entry in enumerate(entries):
        if not isinstance(entry, bytes):
            continue
        name, member = "", str(index)
        for number, value in _proto_fields(entry):
            if number == 1 and isinstance(value, bytes):
                name = value.decode("utf-8")
            elif number == 255 and isinstance(value, int):
                member = str(value)
        members[member] = name
    return members


def _safe_media_name(name: str) -> str | None:
    if not name or name != Path(name).name or name.startswith(".") or "\x00" in name or "\\" in name:
        return None
    return name


def _extract(pin: NoteTypePin, apkg: Path, dest: Path) -> None:
    with zipfile.ZipFile(apkg) as package:
        names = set(package.namelist())
        v3 = "collection.anki21b" in names
        if v3:
            collection = _zstd_decompress(package.read("collection.anki21b"))
        else:
            collection = package.read("collection.anki21" if "collection.anki21" in names else "collection.anki2")
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "collection.db"
            db_path.write_bytes(collection)
            db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            # Anki's own collation, named by the schema-18 indexes.
            db.create_collation("unicase", lambda a, b: (a.casefold() > b.casefold()) - (a.casefold() < b.casefold()))
            try:
                note_type = _read_note_type(db, pin.model, v3=v3)
            finally:
                db.close()
        media_dir = dest / "media"
        media_dir.mkdir(parents=True)
        for member, name in _media_members(package, v3=v3).items():
            safe = _safe_media_name(name)
            if safe is None or member not in names:
                continue
            data = package.read(member)
            if v3 and data.startswith(_ZSTD_MAGIC):
                data = _zstd_decompress(data)
            (media_dir / safe).write_bytes(data)
    (dest / "note_type.json").write_text(json.dumps(note_type, ensure_ascii=False), encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_note_type(pin: NoteTypePin) -> Path:
    """The extracted note type (``note_type.json`` + ``media/``), downloading it once."""
    dest = CACHE / f"{pin.key}-{pin.sha256[:12]}"
    if (dest / "note_type.json").is_file():
        return dest
    apkg = CACHE / "apkg" / Path(urllib.parse.urlsplit(pin.url).path).name
    if not apkg.is_file() or _sha256(apkg) != pin.sha256:
        apkg.parent.mkdir(parents=True, exist_ok=True)
        partial = apkg.with_suffix(".part")
        try:
            with urllib.request.urlopen(pin.url, timeout=60) as response, partial.open("wb") as out:
                shutil.copyfileobj(response, out)
        except OSError as e:
            raise DependencyMissing(f"cannot download {pin.url}: {e}") from e
        if _sha256(partial) != pin.sha256:
            partial.unlink()
            raise RuntimeError(f"{pin.url} does not match its pinned sha256")
        partial.replace(apkg)
    staging = Path(tempfile.mkdtemp(prefix=f"{pin.key}-", dir=CACHE))
    try:
        _extract(pin, apkg, staging / "nt")
        (staging / "nt").replace(dest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return dest


# ---------------------------------------------------------------------------
# Sample fields, built the way mining builds them.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Sample:
    slug: str
    expression: str
    reading: str
    definition: str
    glossary: str
    media: dict[str, bytes]


def _sc(*content: Any) -> dict[str, Any]:
    return {"type": "structured-content", "content": list(content)}


def _el(tag: str, content: Any, *, key: str | None = None, **attrs: Any) -> dict[str, Any]:
    """One structured-content element; ``key`` becomes its ``data-sc-content``."""
    return {"tag": tag, "content": content, **({"data": {"content": key}} if key else {}), **attrs}


def _list(*items: str, key: str = "glossary") -> dict[str, Any]:
    return _el("ul", [_el("li", item) for item in items], key=key, lang="en")


def _row(term: str, reading: str, sequence: int, *glossary: Any, score: int = 0) -> list[Any]:
    """A Yomitan v3 term-bank row: term, reading, tags, rules, score, glossary, sequence, term tags."""
    return [term, reading, "", "", score, list(glossary), sequence, ""]


_ACCENT_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="60" height="20" viewBox="0 0 60 20">'
    b'<path d="M5 15 L20 5 L40 5 L55 15" stroke="black" fill="none" stroke-width="2"/></svg>'
)
_TABLE = _el(
    "details",
    [
        _el("summary", "活用"),
        _el("table", [_el("tr", [_el("th", f), _el("td", v)]) for f, v in (("て形", "行って"), ("た形", "行った"))]),
    ],
)
_IMAGE = {"tag": "img", "path": "img/accent.svg", "appearance": "monochrome", "height": 1.2, "sizeUnits": "em"}
_EXAMPLE = _el(
    "div",
    [
        _el("div", "هذا كتاب.", key="example-sentence-a", lang="ar"),
        _el("div", "This is a book.", key="example-sentence-b"),
    ],
    key="example-sentence",
)

# (title, mining language, styles.css, term rows, media files)
_DICTIONARIES: tuple[tuple[str, str, str | None, list[list[Any]], dict[str, bytes]], ...] = (
    (
        "JMdict [2026-10-17]",
        "ja",
        None,
        [
            _row("猫", "ねこ", 1, "cat"),
            _row("食べる", "たべる", 2, _sc(_list("to eat", "to consume"), _list("usu. written in kana", key="notes"))),
            _row("見る", "みる", 3, "to see", "to look at", score=3),
            _row("見る", "みる", 3, "to watch", score=2),
            _row("見る", "みる", 3, _sc(_list("to try"), _list("after the -te form", key="notes")), score=1),
            _row("橋", "はし", 4, "bridge"),
        ],
        {},
    ),
    ("Jitendex.org [2026-10-03]", "ja", None, [_row("橋", "はし", 4, _sc(_list("bridge", "pons")))], {}),
    ("大辞林　第四版", "ja", None, [_row("橋", "はし", 4, "川・谷・道路などの上にかけ渡して、通路とするもの。")], {}),
    (
        "Styled Dict [v3]",
        "ja",
        '[data-sc-content="tag"] { font-weight: bold; }\n[data-sc-content="example"] { border-left: 2px solid; }',
        [
            _row(
                "走る",
                "はしる",
                5,
                _sc(_el("span", "v5r", key="tag"), _el("div", "毎朝走る。", key="example"), _list("to run")),
            )
        ],
        {},
    ),
    ("活用表 [2026]", "ja", None, [_row("行く", "いく", 6, _sc(_TABLE, _list("to go")))], {}),
    ("Pitch Images [1]", "ja", None, [_row("雨", "あめ", 7, _sc(_IMAGE, " rain"))], {"img/accent.svg": _ACCENT_SVG}),
    ("wty-ar-en", "ar", None, [_row("كتاب", "", 8, _sc(_list("book"), _EXAMPLE))], {}),
)

# slug → (lookup word, reading, mining language)
_SAMPLES: tuple[tuple[str, str, str, str], ...] = (
    ("one-gloss", "猫", "ねこ", "ja"),
    ("glosses-and-note", "食べる", "たべる", "ja"),
    ("three-senses", "見る", "みる", "ja"),
    ("three-dictionaries", "橋", "はし", "ja"),
    ("own-styles", "走る", "はしる", "ja"),
    ("table-details", "行く", "いく", "ja"),
    ("monochrome-image", "雨", "あめ", "ja"),
    ("rtl-example", "كتاب", "", "ar"),
)


def build_samples(work: Path) -> list[Sample]:
    """Fixture dictionaries → import → DefinitionService → attach_card_style_block."""
    sys.path.insert(0, str(REPO))  # tests.fixtures; -I keeps the repo off sys.path
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.languages.registry import get_profile
    from anki_miner.services.definition_service import DefinitionService
    from anki_miner.services.dictionary.card_style_block import attach_card_style_block
    from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
    from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider
    from anki_miner.services.dictionary.yomitan_renderer import dict_media_filename
    from tests.fixtures.dictionary.build_yomitan_fixture import build_yomitan_zip

    providers: dict[str, list[IndexedDictProvider]] = {}
    media: dict[str, bytes] = {}
    for index, (title, language, styles, rows, files) in enumerate(_DICTIONARIES):
        zip_path = build_yomitan_zip(
            work / "zips" / f"{index}.zip",
            title=title,
            revision="1",
            term_banks=[rows],
            tag_banks=[],
            media_files=files or None,
            styles_css=styles,
        )
        result = import_yomitan_zip(zip_path, work / "dicts", language=language)
        provider = IndexedDictProvider(
            result.dict_id,
            work / "dicts" / result.dict_id / "index.sqlite",
            display_name=title,
            keys=get_profile(language).dict_keys,
        )
        providers.setdefault(language, []).append(provider)
        for rel, data in files.items():
            media[dict_media_filename(result.dict_id, rel)] = data

    services = {}
    for language, chain in providers.items():
        config = dataclasses.replace(AnkiMinerConfig(), language=language)
        service = DefinitionService(config, list(chain))
        service.ensure_loaded()
        services[language] = service

    samples = []
    for slug, word, reading, language in _SAMPLES:
        service = services[language]
        entries = service.css_entries()
        direction = get_profile(language).content_style.direction
        definition = service.get_definitions_batch([(word, reading or None)])[0]
        glossary = service.get_glossaries_batch([(word, reading or None)])[0]
        if not definition or not glossary:
            raise RuntimeError(f"sample {slug!r}: no definition for {word!r}")
        samples.append(
            Sample(
                slug=slug,
                expression=word,
                reading=reading or word,
                definition=attach_card_style_block(definition, dict_css_entries=entries, direction=direction),
                glossary=attach_card_style_block(glossary, dict_css_entries=entries, direction=direction),
                media={name: data for name, data in media.items() if name in definition + glossary},
            )
        )
    return samples


def house_selectors() -> frozenset[str]:
    """Every selector group the house sheet (all regions + the RTL rule) emits."""
    from anki_miner.services.dictionary.card_style_block import RTL_GLOSSARY_CSS, _minify_css
    from anki_miner.services.dictionary.card_style_presets import load_glossary_css

    css = _minify_css(load_glossary_css()) + RTL_GLOSSARY_CSS
    return frozenset(_norm_selector(rule.split("{", 1)[0]) for rule in css.split("}") if "{" in rule)


def _norm_selector(text: str) -> str:
    return re.sub(r"\s*,\s*", ",", re.sub(r"\s+", " ", text.strip()))


# ---------------------------------------------------------------------------
# Anki's reviewer: template rendering and the page a card is shown in.
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
_EMPTY_RE = re.compile(r"^(?:\s|&nbsp;|</?(?:br|div) ?/?>)*$", re.IGNORECASE)  # rslib field_is_empty
_FURIGANA_RE = re.compile(r" ?([^ >]+?)\[(.+?)\]")


def _furigana(value: str, part: str) -> str:
    def repl(m: re.Match[str]) -> str:
        if m.group(2).startswith("sound:"):
            return m.group(0)
        if part == "furigana":
            return f"<ruby><rb>{m.group(1)}</rb><rt>{m.group(2)}</rt></ruby>"
        return m.group(2) if part == "kana" else m.group(1)

    return _FURIGANA_RE.sub(repl, value.replace("&nbsp;", " "))


_FILTERS = {
    "text": lambda v: html.unescape(re.sub(r"<[^>]*>", "", re.sub(r"<!--.*?-->", "", v, flags=re.DOTALL))),
    "furigana": lambda v: _furigana(v, "furigana"),
    "kana": lambda v: _furigana(v, "kana"),
    "kanji": lambda v: _furigana(v, "kanji"),
    "hint": lambda v: v,
    "tts": lambda v: "",
    "type": lambda v: "",
}


def render_template(template: str, fields: dict[str, str]) -> str:
    """Anki's mustache subset: ``{{Field}}``, ``{{filter:Field}}``, ``{{#F}}``/``{{^F}}`` sections."""
    root: dict[str, Any] = {"children": []}
    stack = [root]
    last = 0
    for m in _TAG_RE.finditer(template):
        stack[-1]["children"].append(template[last : m.start()])
        last = m.end()
        key = m.group(1).strip()
        if key[:1] in "#^":
            node = {"kind": key[0], "name": key[1:].strip(), "children": []}
            stack[-1]["children"].append(node)
            stack.append(node)
        elif key[:1] == "/":
            name = key[1:].strip()
            for i in range(len(stack) - 1, 0, -1):
                if stack[i].get("name") == name:
                    del stack[i:]
                    break
        else:
            stack[-1]["children"].append({"kind": "var", "key": key})
    stack[-1]["children"].append(template[last:])

    def walk(node: dict[str, Any]) -> str:
        out = []
        for child in node["children"]:
            if isinstance(child, str):
                out.append(child)
            elif child["kind"] == "var":
                *filters, name = child["key"].split(":")
                value = fields.get(name.strip(), "")
                for f in reversed(filters):
                    value = _FILTERS.get(f.strip().split()[0] if f.strip() else "", lambda v: v)(value)
                out.append(value)
            else:
                empty = bool(_EMPTY_RE.match(fields.get(child["name"], "")))
                if (child["kind"] == "#") != empty:
                    out.append(walk(child))
        return "".join(out)

    return walk(root)


_ANKI_VARS = {
    "light": "--fg:#020202;--fg-subtle:#737373;--canvas:#f5f5f5;--canvas-elevated:#fff;--border:#c4c4c4;",
    "dark": "--fg:#fcfcfc;--fg-subtle:#858585;--canvas:#2c2c2c;--canvas-elevated:#363636;--border:#202020;",
}

# Port of Anki's ts/reviewer setInnerHTML + replaceScript: the card HTML goes into
# #qa, its scripts are re-created in order (external ones awaited), then the hooks run.
_REVIEWER_SCRIPT = """
var ankiPlatform = "desktop"; var onUpdateHook = []; var onShownHook = [];
function pycmd() { return false; } globalThis.bridgeCommand = pycmd;
function __replaceScript(old) { return new Promise((resolve) => {
  const s = document.createElement("script");
  if (old.src) { s.addEventListener("load", () => resolve()); s.addEventListener("error", () => resolve()); }
  for (const a of old.attributes) s.setAttribute(a.name, a.value);
  s.appendChild(document.createTextNode(old.innerHTML)); old.replaceWith(s); if (!old.src) resolve(); }); }
async function __updateQA(html) {
  onUpdateHook = []; onShownHook = [];
  const qa = document.getElementById("qa"); qa.innerHTML = html;
  for (const old of qa.getElementsByTagName("script")) await __replaceScript(old);
  for (const fn of onUpdateHook) { try { await fn(); } catch (e) { console.error("hook: " + e); } }
  await new Promise((r) => requestAnimationFrame(() => r()));
  for (const fn of onShownHook) { try { await fn(); } catch (e) { console.error("hook: " + e); } }
}
window.__anki = {
  question: async (q, bodyClass) => { document.body.className = bodyClass; await __updateQA(q); },
  answer: async (a) => { await __updateQA(a); } };
"""


def reviewer_html(theme: str) -> str:
    dark = theme == "dark"
    return (
        f'<!doctype html><html class="{"night-mode" if dark else ""}" lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<style id="anki-reviewer-base">:root {{ {_ANKI_VARS[theme]} color-scheme: {theme}; }}'
        " html { font-family: sans-serif; }"
        " body { margin: 20px; overflow-wrap: break-word; background-color: var(--canvas); color: var(--fg); }"
        " img { max-width: 100%; max-height: 95vh; }</style>"
        f"<script>{_REVIEWER_SCRIPT}</script></head>"
        f'<body class="card card1{" nightMode night_mode" if dark else ""}"><div id="qa"></div></body></html>'
    )


class _MediaServer:
    """Anki's media server stand-in: the note type's media at "/", plus overlays."""

    def __init__(self, media_dir: Path | None, overlays: dict[str, bytes]):
        overlays = dict(overlays)

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - http.server API
                url = urllib.parse.urlsplit(self.path)
                name = urllib.parse.unquote(url.path).lstrip("/")
                if name == "__reviewer__.html":
                    theme = urllib.parse.parse_qs(url.query).get("theme", ["light"])[0]
                    body = reviewer_html("dark" if theme == "dark" else "light").encode()
                elif name in overlays:
                    body = overlays[name]
                elif media_dir is not None and _safe_media_name(name) and (media_dir / name).is_file():
                    body = (media_dir / name).read_bytes()
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("content-type", mimetypes.guess_type(name)[0] or "application/octet-stream")
                self.send_header("cache-control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - http.server API
                pass

        class Server(http.server.ThreadingHTTPServer):
            def handle_error(self, request: Any, client_address: Any) -> None:
                # A closing browser context aborts its in-flight requests.
                if not isinstance(sys.exc_info()[1], ConnectionError):
                    super().handle_error(request, client_address)

        self._server = Server(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


# ---------------------------------------------------------------------------
# Renders: note type × mode. Fields per note type follow its own field names.
# ---------------------------------------------------------------------------


def _furigana_field(expression: str, reading: str) -> str:
    if not reading or reading == expression:
        return expression
    i = 0
    while i < min(len(expression), len(reading)) - 1 and expression[-1 - i] == reading[-1 - i]:
        i += 1
    return f"{expression[: len(expression) - i]}[{reading[: len(reading) - i]}]{expression[len(expression) - i :]}"


def _lapis_fields(s: Sample) -> dict[str, str]:
    return {
        "Expression": s.expression,
        "ExpressionFurigana": _furigana_field(s.expression, s.reading),
        "ExpressionReading": s.reading,
        "Sentence": f"昨日、<b>{s.expression}</b>の話をした。",
        "MainDefinition": s.definition,
        "Glossary": s.glossary,
    }


def _senren_fields(s: Sample) -> dict[str, str]:
    return {
        "word": s.expression,
        "reading": s.reading,
        "sentence": f"昨日、<b>{s.expression}</b>の話をした。",
        "definition": s.definition,
        "glossary": s.glossary,
    }


# Kiku shows one page at a time inside the shadow root of <kiku-host-anki>; its
# pager reads "1/N" and listens for the default ArrowRight keybind.
_KIKU_PAGES = """(() => { const h = document.querySelector('kiku-host-anki'); const roots = [document];
  if (h && h.shadowRoot) roots.push(h.shadowRoot);
  for (const r of roots) for (const el of r.querySelectorAll('*')) { if (el.children.length) continue;
    const m = (el.textContent || '').trim().match(/^1\\/(\\d+)$/); if (m) return Number(m[1]); }
  return 1; })()"""
_KIKU_NEXT = "window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }))"
_SENREN_PAGES = """(() => { const t = document.querySelector('.card-b.show .dictionary-counter')?.textContent || '';
  const m = t.match(/\\/\\s*(\\d+)/); return m ? Number(m[1]) : 1; })()"""
_SENREN_NEXT = "document.querySelector('.card-b.show .toggle-area.right')?.click()"
_LAPIS_PAGES = "document.querySelectorAll('.main-def > .definition > div').length || 1"
_LAPIS_NEXT = "typeof changeIndex === 'function' && changeIndex(1)"


@dataclasses.dataclass(frozen=True)
class Render:
    key: str
    note_type: str
    fields: Any
    pages: str | None = None
    next_page: str | None = None
    # Kiku only: its _kiku_config.json, read at startup.
    config: dict[str, Any] | None = None
    # Only Kiku swaps the page's HTML, so only Kiku is probed page by page.
    probe_pages: bool = False


RENDERS = (
    Render("lapis", "lapis", _lapis_fields, _LAPIS_PAGES, _LAPIS_NEXT),
    Render("amn", "amn", _lapis_fields, _LAPIS_PAGES, _LAPIS_NEXT),
    Render("senren", "senren", _senren_fields, _SENREN_PAGES, _SENREN_NEXT),
    Render("kiku-normal", "kiku", _lapis_fields, _KIKU_PAGES, _KIKU_NEXT, {"definitionStyle": "normal"}, True),
    Render(
        "kiku-single-page", "kiku", _lapis_fields, _KIKU_PAGES, _KIKU_NEXT, {"definitionStyle": "single-page"}, True
    ),
    Render(
        "kiku-glossary-split",
        "kiku",
        _lapis_fields,
        _KIKU_PAGES,
        _KIKU_NEXT,
        {"definitionStyle": "glossary-split"},
        True,
    ),
    Render("basic", "basic", lambda s: {"Front": s.expression, "Back": s.glossary}),
)


# ---------------------------------------------------------------------------
# The probe. CDP lists an element's matched rules in cascade order (cascade
# layer, then specificity, then source order); a house declaration wins when it
# is the last non-!important declaration of its property and no !important or
# inline one follows.
# ---------------------------------------------------------------------------

# Logical longhands → physical, for horizontal left-to-right blocks (every block
# probed here: the RTL rule only sets unicode-bidi on example sentences).
_LOGICAL = {
    **{f"{box}-inline-start": f"{box}-left" for box in ("margin", "padding")},
    **{f"{box}-inline-end": f"{box}-right" for box in ("margin", "padding")},
    **{f"{box}-block-start": f"{box}-top" for box in ("margin", "padding")},
    **{f"{box}-block-end": f"{box}-bottom" for box in ("margin", "padding")},
    **{
        f"border-{axis}-{side}-{part}": f"border-{phys}-{part}"
        for axis, side, phys in (
            ("inline", "start", "left"),
            ("inline", "end", "right"),
            ("block", "start", "top"),
            ("block", "end", "bottom"),
        )
        for part in ("width", "style", "color")
    },
    "inset-inline-start": "left",
    "inset-inline-end": "right",
    "inset-block-start": "top",
    "inset-block-end": "bottom",
    "inline-size": "width",
    "block-size": "height",
    "min-inline-size": "min-width",
    "min-block-size": "min-height",
    "max-inline-size": "max-width",
    "max-block-size": "max-height",
    "overflow-inline": "overflow-x",
    "overflow-block": "overflow-y",
}

_INHERITED = frozenset(
    [
        "color",
        "cursor",
        "direction",
        "font-family",
        "font-size",
        "font-style",
        "font-weight",
        "image-rendering",
        "letter-spacing",
        "line-height",
        "list-style-image",
        "list-style-position",
        "list-style-type",
        "text-align",
        "text-indent",
        "text-transform",
        "visibility",
        "white-space",
        "word-spacing",
    ]
)


@dataclasses.dataclass(frozen=True)
class Decl:
    role: str  # house | nt | dict | inline | anki (the reviewer's own base sheet)
    prop: str
    value: str
    important: bool
    selector: str
    # A zero-specificity rule, or one in a `base` cascade layer (Tailwind's
    # preflight in Kiku), is a reset the note type offers to every later rule:
    # beating it is not overriding a choice the note type made about the glossary.
    reset: bool = False


def _norm_value(value: str) -> str:
    value = re.sub(r"\s+", " ", value.replace("!important", "").strip().lower())
    return re.sub(r"(?<![\w.])0(?:px|em|rem|%)?(?![\w.])", "0", value)


def _declarations(style: dict[str, Any], role: str, selector: str, *, reset: bool = False) -> list[Decl]:
    out: dict[str, Decl] = {}
    for entry in style.get("cssProperties", []):
        if entry.get("disabled") or entry.get("parsedOk") is False:
            continue
        important = bool(entry.get("important")) or "!important" in entry.get("value", "")
        parts = entry.get("longhandProperties") or [entry]
        for part in parts:
            name = str(part["name"])
            prop = _LOGICAL.get(name, name)
            if prop.startswith("--"):
                continue
            out[prop] = Decl(role, prop, part.get("value", ""), important, selector, reset)
    return list(out.values())


def _relative_font_size(value: str) -> bool:
    """A font-size that scales the inherited one rather than replacing it."""
    value = value.strip().lower()
    return value in ("smaller", "larger") or bool(re.fullmatch(r"[\d.]+(?:em|%|ex|ch)", value))


def _winner(decls: list[Decl]) -> Decl | None:
    important = [d for d in decls if d.important]
    if important:
        return important[-1]
    return decls[-1] if decls else None


class Probe:
    """Matched-rule reader for one page over CDP."""

    def __init__(self, cdp: Any, house: frozenset[str], anki_sheets: set[str]):
        self.cdp = cdp
        self.house = house
        self.anki_sheets = anki_sheets

    def _rule_decls(self, matched: list[dict[str, Any]], house_sheets: set[str]) -> list[Decl]:
        decls: list[Decl] = []
        for m in matched:
            rule = m["rule"]
            if rule.get("origin") != "regular":
                continue
            selectors = rule["selectorList"]["selectors"]
            text = _norm_selector(rule["selectorList"]["text"])
            if text in self.house:
                role = "house"
            elif rule.get("styleSheetId") in house_sheets:
                role = "dict"  # a dictionary's scoped styles.css, carried in the house block
            elif rule.get("styleSheetId") in self.anki_sheets:
                role = "anki"
            else:
                role = "nt"
                matching = [selectors[i] for i in m.get("matchingSelectors", [])]
                text = ", ".join(s["text"] for s in matching) or text
                weight = max((tuple(s.get("specificity", {}).get(k, 1) for k in "abc") for s in matching), default=(1,))
                layers = [layer.get("text", "") for layer in rule.get("layers", [])]
                reset = not any(weight) or any(name == "base" or name.startswith("base.") for name in layers)
                decls.extend(_declarations(rule["style"], role, text, reset=reset))
                continue
            decls.extend(_declarations(rule["style"], role, text))
        return decls

    async def element_findings(self, node_id: int) -> tuple[list[dict[str, str]], list[dict[str, str]], bool]:
        """(house-overrides-nt, nt-overrides-house, any house declaration applies) for one element."""
        result = await self.cdp.send("CSS.getMatchedStylesForNode", {"nodeId": node_id})
        matched = result.get("matchedCSSRules", [])
        house_sheets = {
            m["rule"].get("styleSheetId")
            for m in matched
            if _norm_selector(m["rule"]["selectorList"]["text"]) in self.house
        }
        decls = self._rule_decls(matched, house_sheets)
        if result.get("inlineStyle"):
            decls.extend(_declarations(result["inlineStyle"], "inline", "style attribute"))
        overrides, overridden = [], []
        for prop in sorted({d.prop for d in decls if d.role == "house"}):
            same = [d for d in decls if d.prop == prop]
            winner = _winner(same)
            house = [d for d in same if d.role == "house"][-1]
            if winner is not None and winner.role == "nt":
                overridden.append(
                    {"property": prop, "house": house.value, "note_type": winner.value, "selector": winner.selector}
                )
                continue
            if winner is not house:
                continue
            losers = [d for d in same if d.role == "nt" and not d.reset]
            if losers:
                if _norm_value(losers[-1].value) != _norm_value(house.value):
                    overrides.append(
                        {
                            "property": prop,
                            "house": house.value,
                            "note_type": losers[-1].value,
                            "selector": losers[-1].selector,
                            "via": "same element",
                        }
                    )
            elif prop in _INHERITED and not (prop == "font-size" and _relative_font_size(house.value)):
                inherited = self._inherited_winner(result.get("inherited", []), prop, house_sheets)
                if inherited is not None and _norm_value(inherited.value) != _norm_value(house.value):
                    overrides.append(
                        {
                            "property": prop,
                            "house": house.value,
                            "note_type": inherited.value,
                            "selector": inherited.selector,
                            "via": "inheritance",
                        }
                    )
        return overrides, overridden, any(d.role == "house" for d in decls)

    def _inherited_winner(self, inherited: list[dict[str, Any]], prop: str, house_sheets: set[str]) -> Decl | None:
        """The note-type declaration the element would inherit ``prop`` from without the house rule."""
        for ancestor in inherited:  # nearest first
            matched = ancestor.get("matchedCSSRules", [])
            sheets = house_sheets | {
                m["rule"].get("styleSheetId")
                for m in matched
                if _norm_selector(m["rule"]["selectorList"]["text"]) in self.house
            }
            decls = [d for d in self._rule_decls(matched, sheets) if d.prop == prop]
            if ancestor.get("inlineStyle"):
                decls += [
                    d for d in _declarations(ancestor["inlineStyle"], "inline", "style attribute") if d.prop == prop
                ]
            winner = _winner(decls)
            if winner is not None:
                return winner if winner.role == "nt" and not winner.reset else None
        return None


def _attrs(node: dict[str, Any]) -> dict[str, str]:
    raw = node.get("attributes", [])
    return dict(zip(raw[::2], raw[1::2], strict=True))


def _label(node: dict[str, Any]) -> str:
    attrs = _attrs(node)
    classes = sorted(c for c in attrs.get("class", "").split() if c.startswith(("gloss-", "yomitan-", "dictionary-")))
    label = node["localName"] + "".join(f".{c}" for c in classes)
    for attr in ("data-count", "data-dictionary"):
        if attr in attrs:
            label += f"[{attr}]"
    return label


async def miner_elements(cdp: Any) -> list[tuple[int, str]]:
    """``(nodeId, signature)`` for every element of each miner block, document order.

    A block is the ``.yomitan-glossary`` nearest a ``li[data-dictionary-id]``; its
    signature names the block's dictionary and occurrence, then the path inside it.
    """
    doc = await cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
    blocks: dict[int, tuple[dict[str, Any], str]] = {}  # block nodeId → (block, dictionary title)

    def find(node: dict[str, Any], block: dict[str, Any] | None) -> None:
        attrs = _attrs(node)
        if "yomitan-glossary" in attrs.get("class", "").split():
            block = node
        if block is not None and node.get("localName") == "li" and "data-dictionary-id" in attrs:
            blocks.setdefault(block["nodeId"], (block, attrs.get("data-dictionary", "?")))
        for child in node.get("children", []) + node.get("shadowRoots", []):
            find(child, block)

    find(doc["root"], None)
    out: list[tuple[int, str]] = []
    seen: dict[str, int] = {}

    def visit(node: dict[str, Any], path: str) -> None:
        out.append((node["nodeId"], path))
        kids = [k for k in node.get("children", []) if k.get("nodeType") == 1 and k["localName"] not in _SKIP]
        labels = [_label(k) for k in kids]
        for i, (kid, label) in enumerate(zip(kids, labels, strict=True)):
            nth = f":{labels[: i + 1].count(label)}" if labels.count(label) > 1 else ""
            visit(kid, f"{path} > {label}{nth}")

    for block, title in blocks.values():
        seen[title] = seen.get(title, 0) + 1
        visit(block, f"{title}#{seen[title]} {_label(block)}")
    return out


_SKIP = ("style", "script", "template")


# Report-only layout checks, run in the page.
_LAYOUT_JS = """() => {
  const roots = [document];
  for (let i = 0; i < roots.length; i++)
    for (const el of roots[i].querySelectorAll('*')) if (el.shadowRoot) roots.push(el.shadowRoot);
  const blocks = [];
  for (const r of roots) for (const li of r.querySelectorAll('li[data-dictionary-id]')) {
    const b = li.closest('.yomitan-glossary'); if (b && !blocks.includes(b)) blocks.push(b); }
  const name = (el) => el.tagName.toLowerCase() + [...el.classList].filter((c) => c.startsWith('gloss-')).map((c) => '.' + c).join('');
  const gaps = [], markers = [];
  for (const b of blocks) {
    const title = b.querySelector('li[data-dictionary-id]').getAttribute('data-dictionary');
    const lines = [];
    const walker = document.createTreeWalker(b, NodeFilter.SHOW_TEXT);
    for (let t = walker.nextNode(); t; t = walker.nextNode()) {
      if (!t.textContent.trim() || t.parentElement.closest('style')) continue;
      const range = document.createRange(); range.selectNodeContents(t);
      for (const rect of range.getClientRects()) if (rect.height > 0) lines.push({ top: rect.top, bottom: rect.bottom, el: t.parentElement });
    }
    for (const img of b.querySelectorAll('img, table')) { const r = img.getBoundingClientRect(); if (r.height > 0) lines.push({ top: r.top, bottom: r.bottom, el: img }); }
    lines.sort((a, c) => a.top - c.top);
    for (let i = 1; i < lines.length; i++) {
      const prevBottom = Math.max(...lines.slice(0, i).map((l) => l.bottom));
      const gap = lines[i].top - prevBottom, h = lines[i].bottom - lines[i].top;
      if (gap >= 0.9 * h) gaps.push({ block: title, before: name(lines[i].el), gap: Math.round(gap), line: Math.round(h) });
    }
    for (const li of b.querySelectorAll('li')) {
      const cs = getComputedStyle(li);
      if (cs.display !== 'list-item' || cs.listStyleType === 'none' || cs.listStylePosition !== 'outside' || !li.getClientRects().length) continue;
      const pad = parseFloat(getComputedStyle(li.parentElement).paddingLeft) + parseFloat(cs.marginLeft);
      if (pad < parseFloat(cs.fontSize)) markers.push({ block: title, list: name(li.parentElement), marker: cs.listStyleType, gutter_px: Math.round(pad) });
    }
  }
  return { gaps, markers };
}"""

_BLOCKS_READY_JS = """() => { const roots = [document];
  for (let i = 0; i < roots.length; i++)
    for (const el of roots[i].querySelectorAll('*')) if (el.shadowRoot) roots.push(el.shadowRoot);
  return roots.some((r) => r.querySelector('li[data-dictionary-id]')); }"""


async def _network_quiet(page: Any, inflight: set[Any], *, quiet_ms: int = 500, timeout_ms: int = 20000) -> None:
    """Wait until the page has had no request in flight for ``quiet_ms``."""
    quiet = waited = 0
    while quiet < quiet_ms:
        if waited >= timeout_ms:
            raise HarnessError(
                f"network still busy after {timeout_ms // 1000} s: {sorted(r.url for r in inflight)[:3]}"
            )
        await page.wait_for_timeout(50)
        waited += 50
        quiet = quiet + 50 if not inflight else 0


@dataclasses.dataclass
class RenderResult:
    render: str
    sample: str
    overrides: list[dict[str, str]] = dataclasses.field(default_factory=list)
    overridden: list[dict[str, str]] = dataclasses.field(default_factory=list)
    page_errors: list[str] = dataclasses.field(default_factory=list)
    inert_pages: list[int] = dataclasses.field(default_factory=list)  # miner block, no house rule applies
    layout: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    shots: list[str] = dataclasses.field(default_factory=list)


def _unique(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [json.loads(row) for row in sorted({json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows})]


async def _show_card(
    context: Any, server: _MediaServer, theme: str, question: str, answer: str, errors: list[str] | None
) -> Any:
    """Open the reviewer, show the question, then the answer once the front has loaded."""

    async def route(r: Any) -> None:
        if r.request.url.startswith((server.origin, "data:", "blob:")):
            await r.continue_()
        else:
            await r.abort("blockedbyclient")

    await context.route("**/*", route)
    page = await context.new_page()
    inflight: set[Any] = set()
    console: list[str] = []
    page.on("request", lambda r: inflight.add(r))
    page.on("requestfinished", lambda r: inflight.discard(r))
    page.on("requestfailed", lambda r: inflight.discard(r))
    page.on("console", lambda m: console.append(f"{m.type}: {m.text[:200]}"))
    if errors is not None:
        page.on("pageerror", lambda e: errors.append(str(e).splitlines()[0][:200]))
    await page.goto(f"{server.origin}/__reviewer__.html?theme={theme}", wait_until="load")
    body_class = "card card1" + (" nightMode night_mode" if theme == "dark" else "")
    await page.evaluate("([q, c]) => window.__anki.question(q, c)", [question, body_class])
    # Reveal the answer only once the front has loaded, as a person does: Kiku's
    # back aborts the front's in-flight init, and the front then replaces the
    # whole page with "Failed to render card".
    await _network_quiet(page, inflight)
    await page.evaluate("(a) => window.__anki.answer(a)", answer)
    try:
        await page.wait_for_function(_BLOCKS_READY_JS, timeout=20000)
    except Exception as e:
        raise HarnessError(f"no miner block after 20 s ({theme}); console: {console[-4:]}") from e
    await page.evaluate("() => document.fonts.ready.then(() => true)")
    await page.wait_for_timeout(600)
    return page


async def _attach_probe(context: Any, page: Any, house: frozenset[str]) -> Probe:
    cdp = await context.new_cdp_session(page)
    headers: list[dict[str, Any]] = []
    cdp.on("CSS.styleSheetAdded", lambda e: headers.append(e["header"]))
    await cdp.send("DOM.enable")
    await cdp.send("CSS.enable")
    doc = await cdp.send("DOM.getDocument", {"depth": 0})
    base = await cdp.send("DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": "#anki-reviewer-base"})
    owner = (await cdp.send("DOM.describeNode", {"nodeId": base["nodeId"]}))["node"]["backendNodeId"]
    anki_sheets = {h["styleSheetId"] for h in headers if h.get("ownerNode") == owner}
    if not anki_sheets:
        raise HarnessError("the reviewer's base stylesheet was not reported over CDP")
    return Probe(cdp, house, anki_sheets)


async def _probe_page(probe: Probe, page: Any, result: RenderResult, number: int, prefix: str) -> None:
    styled = False
    for node_id, signature in await miner_elements(probe.cdp):
        won, lost, matched = await probe.element_findings(node_id)
        styled = styled or matched
        result.overrides += [{"element": prefix + signature, **entry} for entry in won]
        result.overridden += [{"element": prefix + signature, **entry} for entry in lost]
    if not styled:
        result.inert_pages.append(number)
    found = await page.evaluate(_LAYOUT_JS)
    result.layout += [{"kind": kind, "page": number, **x} for kind in ("gaps", "markers") for x in found[kind]]


async def run_render(
    browser: Any,
    render: Render,
    sample: Sample,
    note_type: dict[str, Any],
    server: _MediaServer,
    house: frozenset[str],
    shots_dir: Path | None,
) -> RenderResult:
    """Probe the light 1024 px render; ``shots_dir`` adds dark and 412 px screenshots."""
    fields = render.fields(sample)
    front = render_template(note_type["front"], fields)
    back = render_template(note_type["back"], {**fields, "FrontSide": front})
    style = f"<style>{note_type['css']}</style>"
    result = RenderResult(render.key, sample.slug)
    variants = [("light", 1024)] + ([("dark", 1024), ("light", 412), ("dark", 412)] if shots_dir else [])
    for theme, width in variants:
        primary = (theme, width) == ("light", 1024)
        context = await browser.new_context(viewport={"width": width, "height": 900}, color_scheme=theme)
        try:
            errors = result.page_errors if primary else None
            page = await _show_card(context, server, theme, style + front, style + back, errors)
            probe = await _attach_probe(context, page, house) if primary else None
            total = max(1, int(await page.evaluate(render.pages))) if render.pages else 1
            for number in range(1, total + 1):
                if number > 1:
                    await page.evaluate(render.next_page)
                    await page.wait_for_timeout(400)
                if probe is not None and (number == 1 or render.probe_pages):
                    await _probe_page(
                        probe, page, result, number, f"p{number}/" if render.probe_pages and total > 1 else ""
                    )
                if shots_dir is not None:
                    path = shots_dir / render.key / f"{sample.slug}.{theme}.{width}.p{number}.png"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    await page.screenshot(path=str(path), full_page=True)
                    result.shots.append(str(path.relative_to(shots_dir)))
        finally:
            await context.close()
    result.overrides = _unique(result.overrides)
    result.overridden = _unique(result.overridden)
    result.page_errors = sorted(set(result.page_errors))
    return result


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------


def _entries(results: list[RenderResult]) -> list[dict[str, str]]:
    rows = [{"render": r.render, "sample": r.sample, **o} for r in results for o in r.overrides]
    return sorted(rows, key=lambda x: (x["render"], x["sample"], x["element"], x["property"]))


def _page_errors(results: list[RenderResult]) -> dict[str, list[str]]:
    errors: dict[str, set[str]] = {}
    for r in results:
        errors.setdefault(r.render, set()).update(r.page_errors)
    return {k: sorted(v) for k, v in sorted(errors.items())}


def write_baseline(results: list[RenderResult]) -> None:
    lines = [
        "{",
        '  "_doc": "scripts/card_css_check.py: each house_overrides_note_type entry is a reviewed place where'
        " a glossary.css declaration beats a pinned note type's own declaration (dd8dc96 class). Regenerate with"
        ' --update-baseline only after reviewing the diff.",',
        '  "pins": ' + json.dumps({p.key: p.sha256 for p in PINS}) + ",",
        '  "page_errors": ' + json.dumps(_page_errors(results), ensure_ascii=False) + ",",
        '  "house_overrides_note_type": [',
    ]
    rows = [json.dumps(e, ensure_ascii=False) for e in _entries(results)]
    lines += [f"    {row}," for row in rows[:-1]] + ([f"    {rows[-1]}"] if rows else [])
    lines += ["  ]", "}", ""]
    BASELINE.write_text("\n".join(lines), encoding="utf-8")


def compare_baseline(results: list[RenderResult]) -> bool:
    if not BASELINE.is_file():
        print(f"no baseline at {BASELINE}; run with --update-baseline after reviewing the output")
        return False
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    ok = True
    if baseline.get("pins") != {p.key: p.sha256 for p in PINS}:
        print("pins changed since the baseline was written: review the diff below, then --update-baseline")
        ok = False
    key = json.dumps
    old = {key(e, ensure_ascii=False, sort_keys=True) for e in baseline.get("house_overrides_note_type", [])}
    new = {key(e, ensure_ascii=False, sort_keys=True) for e in _entries(results)}
    for line in sorted(new - old):
        print(f"+ house overrides note type: {line}")
    for line in sorted(old - new):
        print(f"- no longer overrides:       {line}")
    if old != new:
        ok = False
    errors = _page_errors(results)
    if baseline.get("page_errors") != errors:
        print(f"page errors changed: {json.dumps(errors, ensure_ascii=False)}")
        ok = False
    return ok


def write_report(shots_dir: Path, results: list[RenderResult], samples: list[Sample]) -> None:
    by_key = {(r.render, r.sample): r for r in results}
    out = [
        "<!doctype html><meta charset=utf-8><title>Card CSS check</title><style>body{font:14px sans-serif;margin:16px}"
        "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:6px;vertical-align:top}"
        "img{width:220px;display:block;margin-bottom:4px}pre{white-space:pre-wrap;font-size:11px;max-width:420px}"
        "</style><h1>Card CSS check</h1><p>Light 1024 px first page per cell; links open every shot.</p><table><tr><th>sample</th>"
    ]
    out += [f"<th>{html.escape(r.key)}</th>" for r in RENDERS]
    out.append("</tr>")
    for sample in samples:
        out.append(f"<tr><th>{html.escape(sample.slug)}</th>")
        for render in RENDERS:
            r = by_key[(render.key, sample.slug)]
            first = next((s for s in r.shots if ".light.1024.p1." in s), "")
            links = " ".join(
                f'<a href="{html.escape(s)}">{html.escape(Path(s).stem.split(".", 1)[1])}</a>' for s in r.shots
            )
            notes = {
                "house overrides note type": r.overrides,
                "note type overrides house": r.overridden,
                "layout": r.layout,
                "page errors": r.page_errors,
            }
            text = "\n".join(
                f"{k}:\n" + "\n".join("  " + json.dumps(v, ensure_ascii=False) for v in vs)
                for k, vs in notes.items()
                if vs
            )
            if r.inert_pages:
                text = f"house sheet styles nothing on page(s) {r.inert_pages}\n" + text
            out.append(
                f'<td><a href="{html.escape(first)}"><img src="{html.escape(first)}"></a>{links}<pre>{html.escape(text)}</pre></td>'
            )
        out.append("</tr>")
    out.append("</table>")
    (shots_dir / "report.html").write_text("".join(out), encoding="utf-8")
    findings = [dataclasses.asdict(r) for r in results]
    (shots_dir / "findings.json").write_text(json.dumps(findings, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------------------


async def main_async(args: argparse.Namespace) -> int:
    try:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import async_playwright
    except ImportError:
        raise DependencyMissing("Playwright is not installed (pip install -e '.[card-css]')") from None

    note_types: dict[str, dict[str, Any]] = {"basic": BASIC}
    media_dirs: dict[str, Path | None] = {"basic": None}
    for pin in PINS:
        dest = ensure_note_type(pin)
        note_types[pin.key] = json.loads((dest / "note_type.json").read_text(encoding="utf-8"))
        media_dirs[pin.key] = dest / "media"

    with tempfile.TemporaryDirectory(prefix="card-css-") as work:
        samples = build_samples(Path(work))
    house = house_selectors()
    sample_media = {name: data for s in samples for name, data in s.media.items()}
    servers = {
        r.key: _MediaServer(
            media_dirs[r.note_type],
            {**sample_media, **({"_kiku_config.json": json.dumps(r.config).encode()} if r.config else {})},
        )
        for r in RENDERS
    }
    shots_dir = Path(args.shots).resolve() if args.shots else None
    try:
        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.launch()
            except PlaywrightError as e:
                if "Executable doesn't exist" in str(e):
                    raise DependencyMissing(
                        "Playwright's Chromium is missing (python -m playwright install chromium)"
                    ) from e
                raise
            limit = asyncio.Semaphore(args.jobs)

            async def one(render: Render, sample: Sample) -> RenderResult:
                async with limit:
                    try:
                        return await run_render(
                            browser, render, sample, note_types[render.note_type], servers[render.key], house, shots_dir
                        )
                    except (HarnessError, PlaywrightError) as e:
                        message = f"HARNESS: {str(e).splitlines()[0]}"
                        print(f"{render.key}/{sample.slug}: {message}")
                        return RenderResult(render.key, sample.slug, page_errors=[message])

            results = list(await asyncio.gather(*(one(r, s) for r in RENDERS for s in samples)))
            await browser.close()
    finally:
        for server in servers.values():
            server.close()

    if shots_dir is not None:
        write_report(shots_dir, results, samples)
        print(f"report: {shots_dir / 'report.html'}")
    if args.update_baseline:
        if any(e.startswith("HARNESS:") for r in results for e in r.page_errors):
            print("not writing the baseline: a render failed (HARNESS above)")
            return 1
        write_baseline(results)
        print(f"wrote {BASELINE.relative_to(REPO)}: {len(_entries(results))} entries")
        return 0
    if compare_baseline(results):
        print(f"card CSS matches the baseline ({len(_entries(results))} reviewed entries)")
        return 0
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--update-baseline", action="store_true", help="rewrite the baseline from this run")
    parser.add_argument("--shots", metavar="DIR", help="also write screenshots and report.html to DIR")
    parser.add_argument("--jobs", type=int, default=6, help="renders in parallel (default 6)")
    args = parser.parse_args()
    home = tempfile.mkdtemp(prefix="card-css-home-")
    os.environ["ANKI_MINER_HOME"] = home  # before anki_miner is imported: never the real home
    try:
        return asyncio.run(main_async(args))
    except DependencyMissing as e:
        print(f"card-css: skipped, {e}")
        return DEPENDENCY_MISSING
    finally:
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
