"""Arabic tokenizer without the database: segmentation, the pack probe, the build path."""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.languages.ar import availability, tokenizer
from anki_miner.services.tagger import LockedTagger


def test_segment_keeps_verbatim_runs_and_splits_punctuation_per_character():
    line = "\u0643\u0650\u062a\u064e\u0627\u0628\u064c \u062c\u062f\u064a\u062f\u060c Netflix 2024\u061f!"  # kitaabun jadiid
    runs = list(tokenizer.segment(line))
    assert runs == [
        ("ar", "\u0643\u0650\u062a\u064e\u0627\u0628\u064c"),
        ("ar", "\u062c\u062f\u064a\u062f"),
        ("other", "\u060c"),
        ("word", "Netflix"),
        ("digit", "2024"),
        ("other", "\u061f"),
        ("other", "!"),
    ]
    assert "".join(run for _, run in runs) == line.replace(" ", "")


def test_both_digit_sets_form_digit_runs():
    assert list(tokenizer.segment("\u0663\u0664 \u06f1\u06f2")) == [
        ("digit", "\u0663\u0664"),
        ("digit", "\u06f1\u06f2"),
    ]


def test_a_missing_pack_is_reported_as_the_download_hint():
    assert availability.ar_missing_reason() == availability.AR_PACK_REASON
    with pytest.raises(ImportError, match="Arabic language pack"):
        tokenizer.build_tagger()


def test_build_tagger_reads_the_installed_database(monkeypatch, tmp_path):
    from anki_miner.languages.ar._calima import database
    from anki_miner.services import language_pack_installer

    opened: list[Path] = []
    monkeypatch.setattr(language_pack_installer, "component_path", lambda code, name: tmp_path / code / name)
    monkeypatch.setattr(database, "MorphologyDB", lambda path: opened.append(path) or object())

    tagger = tokenizer.build_tagger()

    assert isinstance(tagger, LockedTagger)
    assert opened == [tmp_path / "ar" / "calima_msa" / "morphology.db"]
    assert availability.ar_missing_reason() is None
