"""F-08 and MS-12 under a non-Japanese mining language (Review Focus 3).

The Spanish parser comes from the profile, so these prove the drop and the
collapse sit in the shared loader, not behind a Japanese-only path.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pysubs2

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language


def _spanish_parser(*, skip: bool):
    config = replace(switch_language(AnkiMinerConfig(), "es"), skip_opening_ending=skip)
    return get_profile("es").create_parser(config)


def _write_ass(path: Path, cues: list[tuple[int, int, str, str]]) -> Path:
    subs = pysubs2.SSAFile()
    for style in sorted({cue[3] for cue in cues}):
        subs.styles[style] = pysubs2.SSAStyle()
    for start, end, text, style in cues:
        subs.append(pysubs2.SSAEvent(start=start, end=end, text=text, style=style))
    subs.save(str(path))
    return path


_EPISODE = [
    (0, 2000, "El perro come pan.", "Default"),
    (2000, 4000, "Cantamos bajo el sol", "OP"),
    (4000, 6000, "La niña lee un libro.", "Default"),
    (6000, 8000, r"{\pos(320,40)}Panadería", "Cartel"),
    (8000, 10000, "Mañana vamos al mercado.", "Default"),
]


def test_spanish_op_style_lines_are_not_mined(tmp_path):
    sub = _write_ass(tmp_path / "ep01.ass", _EPISODE)

    sentences = {word.sentence for word in _spanish_parser(skip=True).parse_subtitle_file(sub, 0.0)}

    assert sentences == {"El perro come pan.", "La niña lee un libro.", "Mañana vamos al mercado."}


def test_spanish_switch_off_keeps_the_song(tmp_path):
    sub = _write_ass(tmp_path / "ep01.ass", _EPISODE)

    sentences = {word.sentence for word in _spanish_parser(skip=False).parse_subtitle_file(sub, 0.0)}

    assert "Cantamos bajo el sol" in sentences


def test_spanish_karaoke_burst_is_one_line(tmp_path):
    frames = [(3000 + 40 * i, 3040 + 40 * i, rf"{{\k{i}}}Cantamos juntos", "Karaoke") for i in range(12)]
    sub = _write_ass(tmp_path / "ep01.ass", [(0, 2000, "El perro come pan.", "Default"), *frames])

    entries = _spanish_parser(skip=False).parse_raw_entries(sub, 0.0)

    assert entries == [(0.0, 2.0, "El perro come pan."), (3.0, 3.48, "Cantamos juntos")]
