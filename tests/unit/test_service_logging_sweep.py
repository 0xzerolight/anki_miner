"""Structural and behavioral checks for the T11 service logging sweep."""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services import alass_installer
from anki_miner.services.known_word_db import KnownWordDB

_SWEPT_MODULES = (
    "anki_miner/services/alass_installer.py",
    "anki_miner/services/asr/_engine.py",
    "anki_miner/services/asr/cuda_pack_installer.py",
    "anki_miner/services/asr/ggml_model_installer.py",
    "anki_miner/services/asr/model_availability.py",
    "anki_miner/services/asr/model_manager.py",
    "anki_miner/services/asr/onnx_pack_installer.py",
    "anki_miner/services/asr/transcriber.py",
    "anki_miner/services/card_backfiller.py",
    "anki_miner/services/deck_filter.py",
    "anki_miner/services/dictionary/importers/jmdict_importer.py",
    "anki_miner/services/dictionary/providers/jisho_provider.py",
    "anki_miner/services/download_resume.py",
    "anki_miner/services/frequency/csv_parse.py",
    "anki_miner/services/frequency/multi_frequency_service.py",
    "anki_miner/services/frequency/render.py",
    "anki_miner/services/known_word_db.py",
    "anki_miner/services/pitch_accent/multi_pitch_service.py",
    "anki_miner/services/pitch_accent_service.py",
    "anki_miner/services/reading/epub_source.py",
    "anki_miner/services/reading/images.py",
    "anki_miner/services/reading/mokuro_source.py",
    "anki_miner/services/resource_downloader.py",
    "anki_miner/services/stats_service.py",
    "anki_miner/services/update_checker.py",
    "anki_miner/services/wordset_service.py",
    "anki_miner/services/yomitan_meta_bank.py",
)


def test_no_swept_module_has_an_unused_logger() -> None:
    root = Path(__file__).resolve().parents[2]
    unused: list[str] = []
    for relative_path in _SWEPT_MODULES:
        tree = ast.parse((root / relative_path).read_text(encoding="utf-8"))
        declares_logger = any(
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "logger" for target in node.targets)
            for node in ast.walk(tree)
        )
        logger_references = sum(isinstance(node, ast.Name) and node.id == "logger" for node in ast.walk(tree))
        if declares_logger and logger_references < 2:
            unused.append(relative_path)

    assert unused == []


def test_alass_stage_failure_logs_exception_type(tmp_path: Path, monkeypatch, caplog) -> None:
    def fail_replace(_source: Path, _target: Path) -> None:
        raise PermissionError("read-only destination")

    monkeypatch.setattr(alass_installer.os, "replace", fail_replace)

    with caplog.at_level(logging.WARNING, logger="anki_miner.services"), pytest.raises(SetupError):
        alass_installer._place_file(tmp_path / "download.part", tmp_path / "alass")

    record = next(record for record in caplog.records if record.getMessage().startswith("Alass install failed:"))
    assert "exc=PermissionError" in record.getMessage()
    assert record.levelno == logging.WARNING
    assert record.name == "anki_miner.services.alass_installer"


def test_known_word_load_logs_count(tmp_path: Path, caplog) -> None:
    db = KnownWordDB(tmp_path / "known_words.sqlite")
    db.initialize()
    db.add_words({"猫", "犬"})

    with caplog.at_level(logging.INFO, logger="anki_miner.services"):
        assert db.get_known_words() == {"猫", "犬"}

    record = next(record for record in caplog.records if record.getMessage().startswith("Known words load done:"))
    assert "rows=2" in record.getMessage()
    assert record.levelno == logging.INFO
    assert record.name == "anki_miner.services.known_word_db"
