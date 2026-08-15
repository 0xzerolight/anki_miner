#!/usr/bin/env python3
"""README translation harness for ``i18n/README.<code>.md``.

The locale list is read from ``anki_miner.gui.i18n`` so the docs can never
drift from the shipped UI languages. Commands::

    python scripts/readme_i18n.py scaffold ja   # new translation from English
    python scripts/readme_i18n.py nav           # re-render every language nav
    python scripts/readme_i18n.py stamp         # re-bless translations vs README.md
    python scripts/readme_i18n.py check         # parity gate (also run by pytest)
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

from anki_miner.gui.i18n import available_languages

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "README.md"
I18N_DIR = ROOT / "i18n"

NAV_START = "<!-- i18n-nav:start -->"
NAV_END = "<!-- i18n-nav:end -->"

STAMP_RE = re.compile(r"<!-- i18n-source: README\.md sha256:([0-9a-f]{16}) -->")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
FENCE_RE = re.compile(r"^```(.*)$")
TAG_RE = re.compile(r"<[^>]+>")
URL_RE = re.compile(r"https?://[^\s)\"'<>]+")
REL_LINK_RE = re.compile(r"\]\((?!https?://|#)([^)]+)\)")
ANCHOR_RE = re.compile(r"\]\(#([^)]+)\)")


# --- locales ---------------------------------------------------------------


def codes() -> list[str]:
    """Translation locale codes, English excluded, in UI order."""
    return [code for code in available_languages() if code != "en"]


def translation_path(code: str) -> Path:
    return I18N_DIR / f"README.{code}.md"


# --- source stamp ----------------------------------------------------------


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def stamp_line(source_text: str) -> str:
    return f"<!-- i18n-source: README.md sha256:{digest(source_text)} -->"


# --- language nav ----------------------------------------------------------


def _nav_href(code: str, current: str) -> str:
    """Link from the ``current`` document to the ``code`` one."""
    if code == "en":
        return "../README.md"
    return f"i18n/README.{code}.md" if current == "en" else f"README.{code}.md"


def render_nav(current: str) -> str:
    """The nav block body as seen from ``current`` ("en" or a locale code)."""
    cells: list[str] = []
    for code, name in available_languages().items():
        if code == current:
            cells.append(f"<b>{name}</b>")
            continue
        cells.append(f'<a href="{_nav_href(code, current)}">{name}</a>')
    return '<p align="center">\n' + " ·\n".join(cells) + "\n</p>"


def nav_body(text: str) -> str:
    start = text.index(NAV_START) + len(NAV_START)
    return text[start : text.index(NAV_END)].strip("\n")


def replace_nav(text: str, current: str) -> str:
    start = text.index(NAV_START) + len(NAV_START)
    end = text.index(NAV_END)
    return f"{text[:start]}\n{render_nav(current)}\n{text[end:]}"


def strip_nav(text: str) -> str:
    start = text.index(NAV_START)
    end = text.index(NAV_END) + len(NAV_END)
    return text[:start] + text[end:]


# --- markdown parsing ------------------------------------------------------


def slugify(heading: str) -> str:
    """GitHub's heading -> anchor slug, close enough for link validation."""
    text = TAG_RE.sub("", heading).strip().lower()
    kept = [ch for ch in text if ch.isalnum() or ch in " -_"]
    return "".join(kept).replace(" ", "-")


def split_fences(text: str) -> tuple[list[str], list[tuple[str, str]]]:
    """Split into (prose lines, [(fence info, body)]) with fences removed."""
    prose: list[str] = []
    blocks: list[tuple[str, str]] = []
    info = ""
    buf: list[str] = []
    inside = False
    for line in text.splitlines():
        match = FENCE_RE.match(line)
        if match and not inside:
            inside, info, buf = True, match.group(1).strip(), []
            continue
        if match and inside:
            blocks.append((info, "\n".join(buf)))
            inside = False
            continue
        (buf if inside else prose).append(line)
    return prose, blocks


def headings(prose_lines: list[str]) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for line in prose_lines:
        match = HEADING_RE.match(line)
        if match:
            out.append((len(match.group(1)), match.group(2)))
    return out


# --- commands --------------------------------------------------------------


def _up(target: str) -> str:
    return target if target.startswith("../") else f"../{target}"


def scaffold(code: str) -> Path:
    """Create ``i18n/README.<code>.md`` from English, ready to translate."""
    source_text = SOURCE.read_text(encoding="utf-8")
    body = REL_LINK_RE.sub(lambda m: f"]({_up(m.group(1))})", source_text)
    body = replace_nav(body, code)
    path = translation_path(code)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{stamp_line(source_text)}\n\n{body}", encoding="utf-8")
    return path


def stamp_all() -> None:
    """Point every translation's stamp at the current README.md."""
    line = stamp_line(SOURCE.read_text(encoding="utf-8"))
    for code in codes():
        path = translation_path(code)
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        if lines and lines[0].startswith("<!-- i18n-source:"):
            lines[0] = line
        else:
            lines = [line, ""] + lines
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def nav_all() -> None:
    """Re-render the nav block in README.md and every translation."""
    SOURCE.write_text(replace_nav(SOURCE.read_text(encoding="utf-8"), "en"), encoding="utf-8")
    for code in codes():
        path = translation_path(code)
        if path.exists():
            path.write_text(replace_nav(path.read_text(encoding="utf-8"), code), encoding="utf-8")


def _anchor_problems(label: str, text: str) -> list[str]:
    prose, _ = split_fences(strip_nav(text))
    slugs = {slugify(title) for _, title in headings(prose)}
    return [
        f"{label}: in-page link #{anchor} matches no heading (slugs: {sorted(slugs)})"
        for anchor in ANCHOR_RE.findall("\n".join(prose))
        if anchor not in slugs
    ]


def check() -> list[str]:
    """Return every parity problem across README.md and its translations."""
    problems: list[str] = []
    source_text = SOURCE.read_text(encoding="utf-8")

    if NAV_START not in source_text or NAV_END not in source_text:
        return [f"README.md: missing {NAV_START} / {NAV_END} markers"]
    if nav_body(source_text) != render_nav("en"):
        problems.append("README.md: nav block is stale - run `python scripts/readme_i18n.py nav`")
    problems += _anchor_problems("README.md", source_text)

    bare = strip_nav(source_text)
    src_prose, src_blocks = split_fences(bare)
    src_levels = [level for level, _ in headings(src_prose)]
    src_urls = sorted(URL_RE.findall(bare))
    src_rel = sorted(REL_LINK_RE.findall(bare))
    src_rows = sum(1 for line in src_prose if line.lstrip().startswith("|"))
    src_html = {tag: bare.count(tag) for tag in ("<details>", "</details>", "<summary>", "</summary>")}
    want_stamp = stamp_line(source_text)

    for code in codes():
        path = translation_path(code)
        label = f"i18n/README.{code}.md"
        if not path.exists():
            problems.append(f"{label}: missing - run `python scripts/readme_i18n.py scaffold {code}`")
            continue

        text = path.read_text(encoding="utf-8")
        first = text.splitlines()[0] if text else ""
        if first != want_stamp:
            problems.append(
                f"{label}: source stamp is stale or missing (line 1 must be `{want_stamp}`). "
                "Re-translate the changed parts of README.md, then run "
                "`python scripts/readme_i18n.py stamp`."
            )
        if NAV_START not in text or NAV_END not in text:
            problems.append(f"{label}: missing nav markers")
            continue
        if nav_body(text) != render_nav(code):
            problems.append(f"{label}: nav block is stale - run `python scripts/readme_i18n.py nav`")

        bare_t = strip_nav(text)
        prose, blocks = split_fences(bare_t)
        levels = [level for level, _ in headings(prose)]
        if levels != src_levels:
            problems.append(f"{label}: heading skeleton differs - English {src_levels}, got {levels}")
        if blocks != src_blocks:
            problems.append(f"{label}: fenced code blocks must be byte-identical to README.md")
        urls = sorted(URL_RE.findall(bare_t))
        if urls != src_urls:
            lost = sorted(set(src_urls) - set(urls))
            extra = sorted(set(urls) - set(src_urls))
            problems.append(f"{label}: URL set differs - missing {lost}, unexpected {extra}")
        rel = sorted(REL_LINK_RE.findall(bare_t))
        if rel != sorted(_up(target) for target in src_rel):
            problems.append(f"{label}: relative links must be the English ones prefixed `../` - got {rel}")
        rows = sum(1 for line in prose if line.lstrip().startswith("|"))
        if rows != src_rows:
            problems.append(f"{label}: table row count differs - English {src_rows}, got {rows}")
        html = {tag: bare_t.count(tag) for tag in src_html}
        if html != src_html:
            problems.append(f"{label}: HTML block counts differ - English {src_html}, got {html}")
        problems += _anchor_problems(label, text)

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    scaffold_parser = sub.add_parser("scaffold", help="create a translation stub from English")
    scaffold_parser.add_argument("code", choices=codes())
    sub.add_parser("nav", help="re-render the language nav block everywhere")
    sub.add_parser("stamp", help="re-point translation stamps at README.md")
    sub.add_parser("check", help="run the structural parity gate")

    args = parser.parse_args(argv)
    if args.command == "scaffold":
        print(scaffold(args.code).relative_to(ROOT))
    elif args.command == "nav":
        nav_all()
    elif args.command == "stamp":
        stamp_all()
    elif args.command == "check":
        problems = check()
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
