"""Unit tests for the README translation harness (scripts/readme_i18n.py)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "readme_i18n.py"
_spec = importlib.util.spec_from_file_location("readme_i18n", _SCRIPT)
assert _spec is not None and _spec.loader is not None
ri = importlib.util.module_from_spec(_spec)
sys.modules["readme_i18n"] = ri
_spec.loader.exec_module(ri)


def test_codes_excludes_english_and_matches_ui_languages() -> None:
    from anki_miner.gui.i18n import available_languages

    assert "en" not in ri.codes()
    assert ri.codes() == [code for code in available_languages() if code != "en"]
    assert "pt_br" in ri.codes() and "zh_tw" in ri.codes()


def test_digest_is_content_addressed() -> None:
    assert ri.digest("a") == ri.digest("a")
    assert ri.digest("a") != ri.digest("b")
    assert len(ri.digest("a")) == 16


def test_stamp_line_round_trips_through_the_stamp_pattern() -> None:
    line = ri.stamp_line("hello")
    match = ri.STAMP_RE.search(line)
    assert match is not None
    assert match.group(1) == ri.digest("hello")


def test_render_nav_marks_current_language_and_links_the_rest() -> None:
    english = ri.render_nav("en")
    assert "<b>English</b>" in english
    assert '<a href="i18n/README.ja.md">日本語</a>' in english
    assert "../" not in english

    japanese = ri.render_nav("ja")
    assert "<b>日本語</b>" in japanese
    assert '<a href="../README.md">English</a>' in japanese
    assert '<a href="README.zh_cn.md">简体中文</a>' in japanese
    assert "i18n/" not in japanese


def test_replace_nav_swaps_only_the_marked_region() -> None:
    text = f"head\n{ri.NAV_START}\nOLD\n{ri.NAV_END}\ntail\n"
    out = ri.replace_nav(text, "ja")
    assert out.startswith("head\n")
    assert out.endswith("tail\n")
    assert "OLD" not in out
    assert ri.nav_body(out) == ri.render_nav("ja")


def test_strip_nav_removes_the_block_entirely() -> None:
    text = f"head\n{ri.NAV_START}\nx\n{ri.NAV_END}\ntail\n"
    assert ri.strip_nav(text) == "head\n\ntail\n"


def test_slugify_matches_github_anchor_rules_including_non_ascii() -> None:
    assert ri.slugify("First-run notes (unsigned builds)") == "first-run-notes-unsigned-builds"
    assert ri.slugify("Notas para la primera ejecución (versiones no firmadas)") == (
        "notas-para-la-primera-ejecución-versiones-no-firmadas"
    )
    assert ri.slugify('<p align="center">Mining Demo</p>') == "mining-demo"


def test_split_fences_separates_code_from_prose() -> None:
    text = "intro\n```bash\npipx install anki-miner\n```\nouttro\n"
    prose, blocks = ri.split_fences(text)
    assert prose == ["intro", "outtro"]
    assert blocks == [("bash", "pipx install anki-miner")]


def test_relative_link_pattern_ignores_urls_and_anchors() -> None:
    text = "[a](CONTRIBUTING.md) [b](https://x.dev/y) [c](#anchor)"
    assert ri.REL_LINK_RE.findall(text) == ["CONTRIBUTING.md"]


def test_scaffold_prefixes_relative_links_and_stamps_the_source(tmp_path, monkeypatch) -> None:
    source = tmp_path / "README.md"
    source.write_text(
        f"# T\n\n{ri.NAV_START}\nx\n{ri.NAV_END}\n\n"
        "See [CONTRIBUTING.md](CONTRIBUTING.md) and [img](https://x.dev/a.png).\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ri, "ROOT", tmp_path)
    monkeypatch.setattr(ri, "SOURCE", source)
    monkeypatch.setattr(ri, "I18N_DIR", tmp_path / "i18n")

    out = ri.scaffold("ja")
    text = out.read_text(encoding="utf-8")

    assert out == tmp_path / "i18n" / "README.ja.md"
    assert text.splitlines()[0] == ri.stamp_line(source.read_text(encoding="utf-8"))
    assert "[CONTRIBUTING.md](../CONTRIBUTING.md)" in text
    assert "https://x.dev/a.png" in text and "../https" not in text
    assert ri.nav_body(text) == ri.render_nav("ja")
