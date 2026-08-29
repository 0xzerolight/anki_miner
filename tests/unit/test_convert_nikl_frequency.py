"""NIKL frequency converter: header declares direction, so the probe never runs.

Fixture shapes come from the Task 3.9 spike against the real archive
(현대 국어 사용 빈도 조사 2, 2005), not from a guess: the primary member is
UTF-16 LE + BOM with CRLF line endings, the columns are
``순위 / 빈도 / 어휘 / 풀이 / 품사``, the tail is ragged because 풀이 carries
tab-separated variant hanja, and headwords carry a two-digit homograph index.
"""

from __future__ import annotations

import csv
import importlib.util
import sys
import unicodedata
from pathlib import Path

from anki_miner.services.frequency.csv_parse import _header_frequency_mode, _is_frequency_header
from anki_miner.services.frequency.mode_probe import resolve_is_occurrence

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "convert_nikl_frequency.py"
_spec = importlib.util.spec_from_file_location("convert_nikl_frequency", _SCRIPT)
assert _spec is not None and _spec.loader is not None
cnf = importlib.util.module_from_spec(_spec)
sys.modules["convert_nikl_frequency"] = cnf
_spec.loader.exec_module(cnf)

# Ledger A1/A2/A4 as the spike recorded them: UTF-16 LE + BOM, tab-delimited,
# CRLF, and 빈도 (index 1) is a raw occurrence count. Regenerate this literal if
# the source layout ever changes.
_FREQ_ROWS = [
    "순위\t빈도\t어휘\t풀이\t품사",
    "1\t42900\t하다01\t\t동",  # homograph indices: one surface form, several rows
    "2\t20925\t하다02\t\t동",
    "3\t980\t학생01\t學生\t명",
    "4\t14\t곡차02\t穀茶\t\t曲茶\t\t麯茶\t명",  # ragged: 9 cells, 품사 last
    "5\t2\t빵80\t80\t명",  # 풀이 is a bare number: a "last numeric cell" rule reads it as the count
    "6\t9\t 노새 \t\t명",  # 어휘 cells carry stray whitespace
    "7\t3\t레짐8\t\t명",  # a single trailing digit is a corpus typo, not an index
]
_FREQ_TSV = "\r\n".join(_FREQ_ROWS) + "\r\n"


def _write_utf16(tmp_path: Path, name: str, text: str) -> Path:
    """Write the primary member's real encoding: UTF-16 LE with a BOM."""
    p = tmp_path / name
    p.write_bytes(text.encode("utf-16"))
    return p


def _write_cp949(tmp_path: Path, name: str, text: str) -> Path:
    """Write a sibling member's encoding (어미통계/음절통계 are cp949)."""
    p = tmp_path / name
    p.write_bytes(text.encode("cp949"))
    return p


# cp949 cannot represent every hanja the UTF-16 member carries (麯 is one), so
# the cp949 case gets its own subset rather than the fixture above.
_CP949_TSV = "\r\n".join([_FREQ_ROWS[0], _FREQ_ROWS[1], _FREQ_ROWS[3], _FREQ_ROWS[6]]) + "\r\n"


def _body(out: Path) -> dict[str, str]:
    return dict(list(csv.reader(out.open(encoding="utf-8")))[1:])


def test_emitted_header_declares_occurrence_direction(tmp_path: Path) -> None:
    out = tmp_path / "nikl.csv"
    rows = cnf.convert(_write_utf16(tmp_path, "freq.tsv", _FREQ_TSV), out)
    header = next(csv.reader(out.open(encoding="utf-8")))
    assert header == ["term", "count"]
    assert rows == len(_body(out))
    # The importer only reads a declaration off a row it recognises as a header.
    assert _is_frequency_header(header)
    assert _header_frequency_mode(header) == "occurrence-based"


def test_declared_direction_short_circuits_the_probe(tmp_path: Path) -> None:
    out = tmp_path / "nikl.csv"
    cnf.convert(_write_utf16(tmp_path, "freq.tsv", _FREQ_TSV), out)
    header, *body = list(csv.reader(out.open(encoding="utf-8")))
    values = {term: (int(count),) for term, count in body}
    # 하다 is a ko probe term but 노새's partner terms are absent, so a probe
    # fallback would answer ambiguous. The declaration must win regardless.
    assert resolve_is_occurrence(_header_frequency_mode(header), values, "ko") is True


def test_counts_are_preserved_not_reranked(tmp_path: Path) -> None:
    out = tmp_path / "nikl.csv"
    cnf.convert(_write_utf16(tmp_path, "freq.tsv", _FREQ_TSV), out)
    # 빈도 (index 1) is the emitted value, never 순위 (index 0).
    assert _body(out) == {
        "하다": "63825",
        "학생": "980",
        "곡차": "14",
        "빵": "2",
        "노새": "9",
        "레짐8": "3",
    }


def test_homograph_indices_are_stripped_and_their_counts_summed(tmp_path: Path) -> None:
    out = tmp_path / "nikl.csv"
    cnf.convert(_write_utf16(tmp_path, "freq.tsv", _FREQ_TSV), out)
    body = _body(out)
    # 하다01 + 하다02: the miner mines the surface form 하다, so the rows merge
    # by sum. First-wins would report 42900 and lose 20925.
    assert body["하다"] == str(42900 + 20925)
    assert "하다01" not in body and "하다02" not in body
    # Exactly two digits are an index; one trailing digit is a corpus typo.
    assert "레짐8" in body and "레짐" not in body


def test_a_numeric_gloss_never_becomes_the_count(tmp_path: Path) -> None:
    out = tmp_path / "nikl.csv"
    cnf.convert(_write_utf16(tmp_path, "freq.tsv", _FREQ_TSV), out)
    # 빵80 carries 풀이 "80": picking the last numeric cell yields the gloss.
    assert _body(out)["빵"] == "2"


def test_ragged_rows_read_by_fixed_index(tmp_path: Path) -> None:
    out = tmp_path / "nikl.csv"
    cnf.convert(_write_utf16(tmp_path, "freq.tsv", _FREQ_TSV), out)
    # 곡차02's 풀이 spills variant hanja across four extra cells; indices 1 and 2
    # are still the count and the term.
    assert _body(out)["곡차"] == "14"


def test_terms_are_stripped_and_nfc(tmp_path: Path) -> None:
    out = tmp_path / "nikl.csv"
    cnf.convert(_write_utf16(tmp_path, "freq.tsv", _FREQ_TSV), out)
    terms = list(_body(out))
    assert "노새" in terms
    assert all(t == unicodedata.normalize("NFC", t.strip()) for t in terms)


def test_a_cp949_member_decodes_too(tmp_path: Path) -> None:
    # The archive mixes encodings: the primary list is UTF-16, its siblings are
    # cp949. BOM-less cp949 bytes decode as UTF-16 garbage instead of raising,
    # so the ladder must not offer UTF-16 to them.
    out = tmp_path / "nikl.csv"
    cnf.convert(_write_cp949(tmp_path, "freq.tsv", _CP949_TSV), out)
    assert _body(out) == {"하다": "42900", "학생": "980", "노새": "9"}


def test_an_undecodable_file_is_reported_not_mojibake(tmp_path: Path) -> None:
    bad = tmp_path / "freq.tsv"
    bad.write_bytes(b"\x81\x00\xff\x81\xfe\x00")
    try:
        cnf.convert(bad, tmp_path / "nikl.csv")
    except SystemExit as exc:
        assert "could not decode" in str(exc)
    else:  # pragma: no cover - the assertion below reports the failure
        raise AssertionError("undecodable bytes were accepted")
