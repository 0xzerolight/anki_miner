"""Tests for the junk-reduction replay harness (scripts/junk_replay.py).

Dev/CI tool: drives the REAL ``SubtitleParserService`` over per-cue reading
units and dumps mined card fronts to TSV. It is NOT part of the app import
surface (it lives in ``scripts/`` outside the ``anki_miner`` package).

The parser is built against an EMPTY ``dicts_root`` under ``tmp_path`` so the
test never touches the real ``~/.anki_miner`` chain (network/filesystem
isolation) — the offline-free replay path. The test asserts TSV shape/liveness
on a tiny synthetic ``.srt``, never exact linguistic values (pinning today's
output would just re-encode the bug the overhaul fixes).
"""

from __future__ import annotations

import csv
import dataclasses
import sys
from pathlib import Path

# scripts/ is not a package; insert the repo root so the module is importable.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from anki_miner.config.config import AnkiMinerConfig  # noqa: E402
from scripts.junk_replay import (  # noqa: E402
    _TSV_COLUMNS,
    ReplayRow,
    build_parser,
    replay_dir,
    write_tsv,
)

_SRT = """1
00:00:01,000 --> 00:00:03,000
猫が好きです。

2
00:00:04,000 --> 00:00:06,000
本を読む。
"""


def _empty_chain_config(tmp_path: Path) -> AnkiMinerConfig:
    """Config whose dicts_root is an empty dir → no offline chain, no real home."""
    dicts_root = tmp_path / "dicts"
    dicts_root.mkdir()
    return dataclasses.replace(AnkiMinerConfig(), dicts_root=dicts_root)


def test_replay_dir_emits_rows(tmp_path: Path) -> None:
    (tmp_path / "ep01.srt").write_text(_SRT, encoding="utf-8")
    parser = build_parser(_empty_chain_config(tmp_path))

    rows = replay_dir(tmp_path, parser)

    assert rows, "expected at least one mined front from the synthetic .srt"
    assert all(isinstance(r, ReplayRow) for r in rows)
    assert all(r.file == "ep01.srt" for r in rows)
    # Liveness only: some verb/noun front is mined, with a non-empty sentence.
    assert any(r.mined_form for r in rows)
    assert all(r.sentence for r in rows)


def test_replay_dir_ignores_non_subtitles(tmp_path: Path) -> None:
    (tmp_path / "ep01.srt").write_text(_SRT, encoding="utf-8")
    (tmp_path / "notes.txt").write_text("これは字幕ではない。", encoding="utf-8")
    parser = build_parser(_empty_chain_config(tmp_path))

    rows = replay_dir(tmp_path, parser)

    assert {r.file for r in rows} == {"ep01.srt"}


def test_write_tsv_roundtrip(tmp_path: Path) -> None:
    rows = [
        ReplayRow(file="ep01.srt", mined_form="猫", lemma="猫", reading="ねこ", sentence="猫が好きです。"),
        ReplayRow(file="ep01.srt", mined_form="読む", lemma="読む", reading="よむ", sentence="本を読む。"),
    ]
    out = tmp_path / "sub" / "replay.tsv"

    write_tsv(out, rows)

    with out.open(encoding="utf-8", newline="") as fh:
        reader = list(csv.reader(fh, delimiter="\t"))
    assert reader[0] == list(_TSV_COLUMNS)
    assert reader[1] == ["ep01.srt", "猫", "猫", "ねこ", "猫が好きです。"]
    assert reader[2] == ["ep01.srt", "読む", "読む", "よむ", "本を読む。"]
