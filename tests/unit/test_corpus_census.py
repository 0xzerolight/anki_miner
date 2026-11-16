"""Tests for the corpus census dev tool (scripts/corpus_census.py).

The census replays the REAL parser, so these tests assert its bookkeeping on a
tiny synthetic library (rows, splits, the reject hook, suspects, count, diff),
never today's linguistic output beyond one plain noun. The parser is built over
an EMPTY ``dicts_root`` under ``tmp_path``: no dictionary, no ``~/.anki_miner``.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

# scripts/ is not a package; insert the repo root so the module is importable.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import corpus_census  # noqa: E402

_SRT = """1
00:00:01,000 --> 00:00:03,000
猫が好きです。

2
00:00:04,000 --> 00:00:06,000
(田中)猫を見た➡

3
00:00:07,000 --> 00:00:09,000
国際会議に行く。
"""


def _library(root: Path, *, second_show_text: str = _SRT) -> Path:
    for show, text in (("Show A", _SRT), ("Show B", second_show_text)):
        folder = root / show / "Season 1"
        folder.mkdir(parents=True)
        (folder / "ep01.srt").write_text(text, encoding="utf-8")
    (root / "Show A" / "notes.txt").write_text("not a subtitle", encoding="utf-8")
    return root


@pytest.fixture
def census(tmp_path: Path) -> Path:
    """A built census of the two-show library; returns its directory."""
    out = tmp_path / "census"
    totals = corpus_census.build(_library(tmp_path / "library"), out, tmp_path / "dicts", cue_sample=1)
    assert totals["files"] == 2
    assert totals["failed"] == 0
    return out


def test_split_is_a_stable_function_of_the_show_name():
    # Pinned: a changed hash would silently move shows between dev and heldout.
    assert corpus_census.split_of("Show A") == "dev"
    assert corpus_census.split_of("Show B") == "heldout"
    shows = [f"show-{i}" for i in range(500)]
    heldout = sum(corpus_census.split_of(show) == "heldout" for show in shows)
    assert 60 <= heldout <= 140


def test_build_writes_one_row_per_file_and_mined_front(census: Path):
    con = sqlite3.connect(census / "census.sqlite")
    files = con.execute("SELECT show, path, split, cues, error FROM files ORDER BY path").fetchall()
    assert [(show, path) for show, path, *_ in files] == [
        ("Show A", "Show A/Season 1/ep01.srt"),
        ("Show B", "Show B/Season 1/ep01.srt"),
    ]
    assert all(cues == 3 and error is None for *_, cues, error in files)
    # 猫 is mined twice in each file, the second time from the labelled cue.
    assert con.execute("SELECT SUM(n), COUNT(DISTINCT file_id) FROM agg WHERE front = '猫'").fetchone() == (4, 2)
    meta = dict(con.execute("SELECT key, value FROM meta"))
    assert meta["files"] == "2"
    assert Path(meta["anki_miner"]).name == "anki_miner"


def test_reject_hook_records_dropped_tokens_with_a_reason_and_cue(census: Path):
    con = sqlite3.connect(census / "census.sqlite")
    rows = con.execute("SELECT surface, pos1, reason, n, first_ms FROM rej WHERE surface = 'が'").fetchall()
    # If _mine_token is renamed or stops receiving the mining pass's record, rej goes empty.
    assert rows
    assert all(pos1 == "助詞" and reason and n >= 1 and first_ms == 1000 for _s, pos1, reason, n, first_ms in rows)
    assert not con.execute("SELECT 1 FROM rej WHERE pos1 = '補助記号'").fetchall()


def test_in_longer_run_marks_a_span_cut_out_of_a_kanji_or_katakana_run():
    assert corpus_census.in_longer_run("国際会議に行く", 0, 2)  # 国際|会議
    assert corpus_census.in_longer_run("国際会議に行く", 2, 4)
    assert not corpus_census.in_longer_run("国際会議に行く", 0, 4)
    assert corpus_census.in_longer_run("ミクロリサーチ社", 0, 3)
    assert not corpus_census.in_longer_run("猫が好き", 0, 1)


def test_residue_counts_what_is_not_plain_japanese():
    keys = corpus_census.residue_keys("(田中)猫を見た➡ ｱｲｽ OK")
    assert keys["speaker label"] == 1
    assert keys["➡"] == 1
    assert keys["half-width kana"] == 3
    assert keys["latin letter"] == 2
    assert not corpus_census.residue_keys("猫が好きです。")


def test_cue_sample_keeps_distinct_dev_sentences_only(census: Path):
    con = sqlite3.connect(census / "census.sqlite")
    dev_files = {row[0] for row in con.execute("SELECT id FROM files WHERE split = 'dev'")}
    sample = con.execute("SELECT text, file_id FROM cue_sample").fetchall()
    assert sample
    assert len({text for text, _ in sample}) == len(sample)
    assert {file_id for _, file_id in sample} <= dev_files


def test_count_reports_each_split(census: Path):
    counted = corpus_census.count(census, "front = '猫'")
    assert sum(n for n, _shows, _files in counted.values()) == 4
    assert set(counted) <= {"dev", "heldout"}
    assert corpus_census.count(census, "pos1 = '助詞'", table="rejected")
    examples = corpus_census.selected_examples(census, "front = '猫'", "occ", 5, seed=1)
    assert examples
    assert all("猫" in row["sentence"] for row in examples)


def test_suspects_writes_every_lens_file(census: Path, tmp_path: Path):
    suppress = tmp_path / "rulings.tsv"
    suppress.write_text("lens\tkey\truling\nT2\t➡\tknown\n", encoding="utf-8")
    written = corpus_census.write_suspects(census, tmp_path / "dicts", suppress=suppress)
    assert set(written) == {"F1", "F2", "F3", "T1", "T2"}
    residue = [
        json.loads(line) for line in (census / "suspects" / "T2-residue.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert "➡" not in {row["key"] for row in residue}
    assert [row["rank"] for row in residue] == list(range(1, len(residue) + 1))
    brief = (census / "suspects" / "T2-residue.txt").read_text(encoding="utf-8").splitlines()
    assert brief[0].startswith("## residue (")
    assert len(brief) >= len(residue) + 1


def test_a_ruling_hides_rejected_tokens_by_part_of_speech(census: Path, tmp_path: Path):
    assert corpus_census._ruled(
        {"lens": "F3", "key": "居る", "pos1": "動詞", "pos2": "非自立可能"}, {("F3", "pos2:非自立可能")}
    )
    assert corpus_census._ruled({"lens": "F2", "key": "猫"}, {("*", "猫")})
    assert not corpus_census._ruled({"lens": "F2", "key": "猫"}, {("F3", "猫")})


def test_clip_brackets_the_surface_inside_a_short_window():
    sentence = "あ" * 40 + "猫" + "い" * 40
    clipped = corpus_census._clip(sentence, "猫", width=20)
    assert "【猫】" in clipped
    assert len(clipped) < 30


def test_diff_is_empty_for_one_build_and_names_what_a_change_lost(census: Path, tmp_path: Path):
    same = corpus_census.diff(census, census, split="all")
    assert not any(same[key] for key in ("added", "lost", "recounted", "reread"))

    other = tmp_path / "census-b"
    library = _library(tmp_path / "library-b", second_show_text=_SRT.replace("猫", "犬"))
    corpus_census.build(library, other, tmp_path / "dicts")
    changed = corpus_census.diff(census, other, split="all", sample=5)
    assert ("犬", "犬") in {(front, lemma) for _shows, _n, front, lemma in changed["added"]}
    assert ("猫", "猫") in {(front, lemma) for _delta, _before, _after, front, lemma in changed["recounted"]}
    assert changed["sample"]
    assert all(row["before"] != row["after"] for row in changed["sample"])


def test_probe_reports_tokens_mined_words_and_rejects(tmp_path: Path):
    parser, _definitions = corpus_census.build_parser(tmp_path / "dicts")
    sink: list = []
    corpus_census.hook_rejects(parser, sink)
    report = corpus_census.probe(parser, sink, "猫が好きです。")
    assert report["sentence"] == "猫が好きです。"
    assert any(token.startswith("猫/") for token in report["tokens"])
    assert any(line.startswith("猫→猫") for line in report["mined"])
    assert any(line.startswith("が ") for line in report["rejected"])
