"""Tests for the i18n translation-payload-loss gate (scripts/i18n_payload_check.py).

These build tiny ``.ts`` fixtures in ``tmp_path`` and stub the git-show reader
seam, so no real git or repo catalogs are touched.
"""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "i18n_payload_check.py"
_spec = importlib.util.spec_from_file_location("i18n_payload_check", _SCRIPT)
assert _spec is not None and _spec.loader is not None
pc = importlib.util.module_from_spec(_spec)
# Register before exec so dataclass string-annotation resolution
# (from __future__ import annotations) can find the module namespace.
sys.modules["i18n_payload_check"] = pc
_spec.loader.exec_module(pc)


# --- fixture builders -------------------------------------------------------


def _msg(source: str, translation: str, *, numerus: bool = False) -> str:
    attr = ' numerus="yes"' if numerus else ""
    return f"<message{attr}><source>{source}</source>{translation}</message>"


def _ctx(name: str, messages: list[str]) -> str:
    return f"<context><name>{name}</name>{''.join(messages)}</context>"


def _ts(body: str, lang: str = "de_DE") -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE TS>\n' f'<TS version="2.1" language="{lang}">{body}</TS>\n'
    )


TRANSLATED = "<translation>Hallo</translation>"
UNFINISHED_EMPTY = '<translation type="unfinished"/>'
NUMERUS_TRANSLATED = (
    "<translation><numerusform>ein Wort</numerusform>" "<numerusform>%n Wörter</numerusform></translation>"
)
NUMERUS_EMPTY = '<translation type="unfinished"><numerusform/></translation>'


def _write(tmp_path: Path, name: str, xml: str) -> Path:
    p = tmp_path / name
    p.write_text(xml, encoding="utf-8")
    return p


def _reader(base_map: dict[str, str]):
    def read(ts_path: Path) -> str:
        return base_map.get(ts_path.name, "")

    return read


# --- parse_catalog / _translation_is_nonempty ------------------------------


def test_parse_counts_translated_and_unfinished():
    xml = _ts(
        _ctx(
            "Ctx",
            [
                _msg("Hello", TRANSLATED),
                _msg("Bye", UNFINISHED_EMPTY),
                _msg("Found %n word(s)", NUMERUS_TRANSLATED, numerus=True),
            ],
        )
    )
    payload = pc.parse_catalog(xml)
    assert payload.translated == {"Hello": 1, "Found %n word(s)": 1}
    assert payload.unfinished == {"Bye": 1}


def test_parse_empty_input_is_empty_payload():
    payload = pc.parse_catalog("")
    assert not payload.translated
    assert not payload.unfinished


def test_numerus_all_empty_forms_not_translated():
    xml = _ts(_ctx("Ctx", [_msg("Found %n word(s)", NUMERUS_EMPTY, numerus=True)]))
    payload = pc.parse_catalog(xml)
    assert not payload.translated
    assert payload.unfinished == {"Found %n word(s)": 1}


def test_source_counted_across_contexts():
    xml = _ts(_ctx("A", [_msg("Shared", TRANSLATED)]) + _ctx("B", [_msg("Shared", TRANSLATED)]))
    payload = pc.parse_catalog(xml)
    assert payload.translated == {"Shared": 2}


# --- run_check end-to-end ---------------------------------------------------


def test_pass_on_identical_catalogs(tmp_path, capsys):
    xml = _ts(_ctx("Ctx", [_msg("Hello", TRANSLATED)]))
    p = _write(tmp_path, "anki_miner_de.ts", xml)
    rc = pc.run_check("BASE1234567890", [p], _reader({"anki_miner_de.ts": xml}))
    out = capsys.readouterr().out
    assert rc == 0
    assert out.startswith("OK   anki_miner_de.ts")


def test_fail_on_dropped_payload(tmp_path, capsys):
    base = _ts(_ctx("Ctx", [_msg("Hello", TRANSLATED)]))
    # Working tree: the string is now an empty unfinished stub -> payload lost.
    work = _ts(_ctx("Ctx", [_msg("Hello", UNFINISHED_EMPTY)]))
    p = _write(tmp_path, "anki_miner_de.ts", work)
    rc = pc.run_check("BASE", [p], _reader({"anki_miner_de.ts": base}))
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL anki_miner_de.ts" in out
    assert "lost translation (x1): 'Hello'" in out
    # Same string reappears as a new unfinished stub for a previously-translated
    # source, so that signal fires too.
    assert "new unfinished stub for previously-translated: 'Hello'" in out


def test_pass_on_context_move_with_carried_payload(tmp_path, capsys):
    # Base: source lives (translated) in context Old.
    base = _ts(_ctx("OldDialog", [_msg("Save changes?", TRANSLATED)]))
    # Working: same source moved to context New, translation carried along.
    work = _ts(_ctx("NewDialog", [_msg("Save changes?", TRANSLATED)]))
    p = _write(tmp_path, "anki_miner_de.ts", work)
    rc = pc.run_check("BASE", [p], _reader({"anki_miner_de.ts": base}))
    out = capsys.readouterr().out
    assert rc == 0
    assert out.startswith("OK   anki_miner_de.ts")


def test_fail_on_context_move_without_carried_payload(tmp_path, capsys):
    # Base: translated in OldDialog.
    base = _ts(_ctx("OldDialog", [_msg("Save changes?", TRANSLATED)]))
    # Working: pylupdate re-created it EMPTY in NewDialog (payload not moved).
    work = _ts(_ctx("NewDialog", [_msg("Save changes?", UNFINISHED_EMPTY)]))
    p = _write(tmp_path, "anki_miner_de.ts", work)
    rc = pc.run_check("BASE", [p], _reader({"anki_miner_de.ts": base}))
    out = capsys.readouterr().out
    assert rc == 1
    assert "lost translation (x1): 'Save changes?'" in out


def test_new_unfinished_stub_flags_previously_translated(tmp_path, capsys):
    # Base: translated once in ContextA.
    base = _ts(_ctx("ContextA", [_msg("Reuse me", TRANSLATED)]))
    # Working: still translated in ContextA (net zero), but a spurious empty
    # unfinished stub appeared in ContextB. Net regression is zero, yet the
    # new-unfinished signal must fire.
    work = _ts(
        _ctx("ContextA", [_msg("Reuse me", TRANSLATED)]) + _ctx("ContextB", [_msg("Reuse me", UNFINISHED_EMPTY)])
    )
    p = _write(tmp_path, "anki_miner_de.ts", work)
    rc = pc.run_check("BASE", [p], _reader({"anki_miner_de.ts": base}))
    out = capsys.readouterr().out
    assert rc == 1
    assert "new unfinished stub for previously-translated: 'Reuse me'" in out
    # No net regression line, since the translated count held steady.
    assert "lost translation" not in out


def test_numerus_payload_loss_detected(tmp_path):
    base = _ts(_ctx("Ctx", [_msg("Found %n word(s)", NUMERUS_TRANSLATED, numerus=True)]))
    work = _ts(_ctx("Ctx", [_msg("Found %n word(s)", NUMERUS_EMPTY, numerus=True)]))
    p = _write(tmp_path, "anki_miner_de.ts", work)
    rc = pc.run_check("BASE", [p], _reader({"anki_miner_de.ts": base}))
    assert rc == 1


def test_new_catalog_absent_at_base_passes(tmp_path):
    work = _ts(_ctx("Ctx", [_msg("Hello", TRANSLATED)]))
    p = _write(tmp_path, "anki_miner_de.ts", work)
    # read_base returns "" -> the catalog did not exist at base; nothing lost.
    rc = pc.run_check("BASE", [p], _reader({}))
    assert rc == 0


def test_source_catalog_all_unfinished_passes(tmp_path):
    # Mirrors anki_miner_en.ts: every entry unfinished/empty at base AND now.
    src = _ts(
        _ctx(
            "Ctx",
            [_msg("Hello", UNFINISHED_EMPTY), _msg("Bye", UNFINISHED_EMPTY)],
        ),
        lang="en",
    )
    p = _write(tmp_path, "anki_miner_en.ts", src)
    rc = pc.run_check("BASE", [p], _reader({"anki_miner_en.ts": src}))
    assert rc == 0


# --- compare() unit ---------------------------------------------------------


def test_compare_multiset_partial_regression():
    base = pc.CatalogPayload(translated=Counter({"X": 2}))
    work = pc.CatalogPayload(translated=Counter({"X": 1}))
    lost, newly = pc.compare(base, work)
    assert lost == [("X", 1)]
    assert newly == []


def test_compare_no_regression_when_count_holds():
    base = pc.CatalogPayload(translated=Counter({"X": 1}))
    work = pc.CatalogPayload(translated=Counter({"X": 1}))
    lost, newly = pc.compare(base, work)
    assert lost == []
    assert newly == []


def test_compare_preexisting_unfinished_not_flagged():
    # Source unfinished at base and unchanged now, never translated -> silent.
    base = pc.CatalogPayload(unfinished=Counter({"X": 1}))
    work = pc.CatalogPayload(unfinished=Counter({"X": 1}))
    lost, newly = pc.compare(base, work)
    assert lost == []
    assert newly == []


def test_unresolvable_base_ref_errors() -> None:
    """A typo'd --base must error out, not silently pass against nothing."""
    from scripts.i18n_payload_check import main

    assert main(["--base", "definitely-not-a-real-ref"]) == 2
